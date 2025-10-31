from network import Protocol, StreamSocket
import threading
import queue
import struct
import sys
import os
import random

# Reserved protocol number for experiments; see RFC 3692
IPPROTO_RDT = 0xfe
Q_SIZE = 10
PRECHK_HDR_FRMT = '!HHIIBH'
HDR_FRMT = '!HHIIBHB'
HDR_SIZE = struct.calcsize(HDR_FRMT)

class RDTSocket(StreamSocket):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Other initialization here
        
        self.state = 'CLOSED' # Can be CLOSED, CONNECTING, CONNECTED, LISTENING

        self.port = None
        self.remote_addr = None #tuple (ip, port) 
        self.parent: RDTSocket = None #listening socket
        self.lock = threading.Lock()
        self.seg_q = queue.Queue() #TODO - change to seg_q
        self.conn_q = queue.Queue() #stores (socket, addr, port) items 

    def bind(self, port):
        print(f"bind: attempting to bind {port}.")
        #lock
        self.proto.lock.acquire()
        #if in use by another system
        if(self.proto.bound_ports.get(port) != None):
            raise StreamSocket.AddressInUse
        #check if alr connected
        if(self.state == 'CONNECTED'):
            raise StreamSocket.AlreadyConnected
        
        #set fields and bind
        self.proto.bound_ports[port] = self
        self.proto.lock.release()

        self.lock.acquire()
        self.port = port
        self.state = "BOUND"
        self.lock.release()
        

    def listen(self):
        
        print("listen: arrived")
        #err checking
        if(self.port == None):
            raise StreamSocket.NotBound
        if(self.state == 'CONNECTED'):
            raise StreamSocket.AlreadyConnected
        
        #add to protocol server sockets and change state
        self.proto.lock.acquire()
        self.proto.server_sockets[self.port] = self
        self.proto.lock.release()

        self.lock.acquire()
        self.state = 'LISTENING'
        print(f"listen: listening on {self.port}")
        self.lock.release()

    def accept(self):

        print(f'accept: arrived at port - ({self.port})')
        if(self.state != 'LISTENING'):
            raise StreamSocket.NotListening
        
        print('accept: checking q for connection')
        new_sock, rhost, rport = self.conn_q.get(5) #check connection q for new conns
        print('accept: connection accepted')
        
        #set state
        new_sock.lock.acquire()
        new_sock.state = 'CONNECTED'
        new_sock.lock.release()

        return (new_sock, (rhost, rport)) #return conn
        

    def connect(self, addr):
        print(f"connect: self port - {self.port} arrived w/ address: {addr}")
        #exceptions
        if(self.state == 'CONNECTED'):
            raise StreamSocket.AlreadyConnected
        if(self.state == 'LISTENING'):
            raise StreamSocket.AlreadyListening
        
        #reset instance vars
        print('connect: waiting for sock lock')
        self.lock.acquire()
        print('connect: sock lock acquired')
        self.remote_addr = addr
        print('connect: sock lock released')
        self.lock.release()

        #handle port not bound
        if(self.port == None):
            #ephem range - 49152 to 65535
            rand_port = random.randint(49152, 65535)
            while(self.proto.bound_ports.get(rand_port) != None):
                rand_port = random.randint(49152, 65535)
            self.port = rand_port
        
        print(f"connect: self port - {self.port}")
        #assemble SYN segment
        flags = 0 | 1 << 1 | 0 << 2
        seq_num = random.randint(0,4294967295)
        data_len = 0

        precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], seq_num, 0, flags, data_len)
        checksum = get_checksum(precheck)
        syn_seg = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], seq_num, 0, flags, data_len, checksum[0])

        #keep sending SYN until SYNACK is recieved
        segment = None
        while(segment == None):
            print('connect: sending SYN')
            print(f'connect: storing connection - ({self.port}, {addr[0]}, {addr[1]})')
            self.proto.connecting_socks[(self.port, addr[0], addr[1])] = self #store connection in connecting table
            self.state = 'CONNECTING'

            #wait for SYNACK
            try:
                self.output(syn_seg, addr[0])
                segment = self.seg_q.get(timeout=10) #TODO = change to proper timeout
            except queue.Empty: 
                segment = None
                continue
            
            print("connect: Something put in q!")
            rport, _, seq_num, ack_num, flags, _, _= struct.unpack(HDR_FRMT, segment[:HDR_SIZE])

            #TODO - verify seq number

            #if not SYN ACK
            if(flags != 3):
                segment = None
                print("connect: incorrect response")
            else:
                print("connect: ACK recieved!")
            

        #assemble ACK segment        
        flags = 1 | 0 << 1 | 0 << 2 #just doing this for myself lol
        data_len = 0
        precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], 0, seq_num + 1, flags, data_len)
        checksum = get_checksum(precheck)
        ack_seg = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], 0, seq_num + 1, flags, data_len, checksum[0])

        #move from connecting to connected
        self.proto.lock.acquire()
        self.proto.connecting_socks.pop((self.port, addr[0], addr[1]))
        self.proto.connected_socks[(self.port, addr[0], addr[1])] = self
        self.proto.lock.release()

        self.state = "CONNECTED"

        #send ACK and mark connected
        #TODO - what happens is the ACK gets dropped?
        print("connect: Sending ACK!")
        self.output(ack_seg, addr[0])

    def send(self, data):
        '''
        stop and wait -> only finish the send once the ACK is recieved 
        resend after _ s
        '''

        print(f"({self.port}) send: arrived")
        
        #check if connected
        if(self.state != 'CONNECTED'):
            raise StreamSocket.NotConnected
        
        #create segment
        flags = 0
        precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], 0, 0, flags, len(data)) #TODO ACK and Seq nums
        checksum = get_checksum(precheck + data)
        hdr = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], 0, 0, flags, len(data), checksum[0])

        #send

        #wait for ack 
        ack_seg = None
        while ack_seg is None:
            try:
                self.output(hdr+data, self.remote_addr[0])
                ack_seg = self.seg_q.get(timeout=5)
            except queue.Empty:
                print(f"({self.port}) send: nothing in q")
                # loop again until we receive an ACK
                pass

        print(f"({self.port}) send: ack recieved")



    def handle_segment(self, seg, rhost):

        print("Handle Segment: arrived")
        rport, dport, seq_num, ack_num, flags, data_len, checksum = struct.unpack(HDR_FRMT, seg[:HDR_SIZE])
        print(f"Handle Segment: rport-{rport} dport-{dport} seq-{seq_num} ack-num{ack_num} flags-{flags} checksum-{checksum}")
        
        #handle the SYN case 
        if(flags == 2): ##SYN flag set 
            print("Handle Segment: SYN Packet Recieved")
            #check if specified port is listening 
            dest_sock: RDTSocket = self.proto.bound_ports.get(dport)

            #raise exception if port isn't bound or isn't listening 
            if(dest_sock == None):
                print(f"Handle Segment: port {dport} is not bound")
                raise Exception
            
            print(f"Handle Segment: dest sock state: {dest_sock.state}")
            if(dest_sock.state != 'LISTENING'):
                raise StreamSocket.NotListening
            
            #create new socket for conn
            new_sock: RDTSocket = self.proto.socket()
            new_sock.port = self.port
            new_sock.parent = self
            new_sock.remote_addr = (rhost, rport)
            new_sock.state = 'CONNECTING'
            self.proto.connecting_socks[(self.port, rhost, rport)] = new_sock

            #send SYN ACK
            flags = 1 | 1 << 1 | 0 << 2
            data_len = 0
            ack = random.randint(0,10000)
            pre_check = struct.pack(PRECHK_HDR_FRMT, new_sock.port, rport, ack, seq_num + 1, flags, data_len)
            checksum = get_checksum(pre_check)
            synack_seg = struct.pack(HDR_FRMT, new_sock.port, rport, ack, seq_num + 1, flags, data_len, checksum[0])
            self.output(synack_seg, rhost) 
            return
        
        #handle SYN ACK recieved
        elif(flags == 3): 
            print("Handle Segment: SYN ACK Recieved")
            #pass off to connecting port q
            self.seg_q.put(seg)
            return
        
        #handle non-handshake ACK
        elif(flags == 1):
            print("Handle Segment: Non-Handshake Recieved") 
            #pass to q that's waiting 
            self.seg_q.put(seg)
        
        #handle data recieved
        else:
            #deliver data
            print("Handle Segment: Delivering data")
            self.deliver(seg[HDR_SIZE:])

            # assemble ACK
            ack_num = seq_num + 1
            data_len = 0
            flags = 1

            # PRECHK_HDR_FRMT layout is: srcport, dstport, seq, ack, flags, datalen
            precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], 0, ack_num, flags, data_len)
            checksum = get_checksum(precheck)
            seg = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], 0, ack_num, flags, data_len, checksum[0])

            # send ACK
            print("Handle Segment: Data delivered, sending ACK ")
            self.output(seg, self.remote_addr[0])

    

class RDTProtocol(Protocol):
    PROTO_ID = IPPROTO_RDT
    SOCKET_CLS = RDTSocket
    

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Other initialization here
        self.bound_ports = {} #bool dictionary if ports are being used port # -> threading.Event
        self.connecting_socks = {} #dict stores sockets waiting for S SA A handshake ACK (remip, remport, sport) -> socket
        self.connected_socks = {} #dict stores sockets that have finished handshake
        self.server_sockets = {}
        self.lock = threading.Lock()
        
    #TODO - add locks
    #TODO - handle handshake ACKs 
    def input(self, seg, rhost):
        #we want to perform err check and then send to proper socket
        #extract fields
        try:
            print("Proto Input: arrived")
            rport, dport, seq_num, ack_num, flags, data_len, checksum = struct.unpack(HDR_FRMT, seg[:HDR_SIZE])
            print(f"Proto Input: rport-{rport} dport-{dport} seq-{seq_num} ack-num{ack_num} flags-{flags} datalen-{data_len} checksum-{checksum}")
        except Exception as e: 
            print(f"Proto Input: err while unpacking segment - {e}")
            return
        
        # Prevent loopback: ignore packets sent by this same host+port
        if (rhost, rport) == (self.host.ip, dport):
            print(f"Proto Input: loopback detected on ({rhost}, {rport}) — dropping packet")
            return
        #err check
        if(not verify_checksum(seg)):
            print('wrong checksum')
            #drop packet
            return
        
        #check if SYN
        if(flags == 2):
            print("Proto Input: SYN Recieved")
            #demux to listening ports
            dest_sock = self.server_sockets.get(dport)
            if(dest_sock == None):
                raise RDTSocket.NotBound()
            dest_sock.handle_segment(seg, rhost)
            return
        #check if SYNACK 
        elif(flags == 3):
            print(f"Proto Input: SYNACK - looking for key ({dport}, {rhost}, {rport})")
            print(f"Proto Input: Available keys: {list(self.connecting_socks.keys())}")
            dest_sock = self.connecting_socks.get((dport, rhost, rport))

            if(dest_sock == None):
                print(f"Proto Input: Conn not found - ({dport}, {rhost}, {rport})")
                return

            print(f'Proto Input: placing SYNACK on {dest_sock.port}\'s q ')
            dest_sock.seg_q.put(seg)
            return



        #check if handshake ACK 

        print(f"Proto Input: ACK - looking for key ({dport}, {rhost}, {rport})")
        print(f"Proto Input: Available connecting keys: {list(self.connecting_socks.keys())}")
        dest_sock: RDTSocket = self.connecting_socks.pop((dport, rhost, rport), None)

        if(flags == 1 and dest_sock != None):

            print("Proto Input: handshake ACK found!")

            #place on accepting queue 
            self.connected_socks[(dport, rhost, rport)] = dest_sock
            dest_sock.parent.conn_q.put((dest_sock, rhost, rport))
        else: #data recieved
            print(f"Proto Input: no mid-handshake found for ({dport}, {rhost}, {rport}), flags={flags}")

            #check if there's a conn
            dest_sock = self.connected_socks.get((dport, rhost, rport))
            if(dest_sock == None):
                print(f"Proto Input: no connected tuple found for ({dport}, {rhost}, {rport}), flags={flags}")
                return
            
            #pass to socket
            print(f"Proto Input: handing off to socket")
            dest_sock.handle_segment(seg, rhost)
            


                
##HELPERS##

def get_checksum(precheck: bytes) -> bytes:
    """Return 1-byte checksum as bytes object."""
    checksum_val = (~(sum(precheck) & 0xFF)) & 0xFF  # invert and mask to 1 byte
    print(f"checksum val: {checksum_val}")
    print(f"precheck no and: {sum(precheck)}")
    print(f"precheck sum: {sum(precheck) & 0xFF}")
    temp = (sum(precheck)& 0xFF ) + (~(sum(precheck)& 0xFF))
    print(f"temp: {temp}")
    print(f"0xFF: {0xFF}")
    return bytes([checksum_val])


def verify_checksum(segment: bytes) -> bool:
    """Verify simple 1-byte checksum."""
    total = sum(segment) & 0xFF
    print(f"total: {total}")
    return total == 0xFF