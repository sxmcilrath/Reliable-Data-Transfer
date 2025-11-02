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

        #packet drop info
        self.seq_num = 0
        self.last_recieved_num = 0

        self.ack_event = threading.Event()

    def bind(self, port):
        ###print(f"bind: attempting to bind {port}.")
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
        
        ###print("listen: arrived")
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
        ###print(f"listen: listening on {self.port}")
        self.lock.release()

    def accept(self):

        ###print(f'accept: arrived at port - ({self.port})')
        if(self.state != 'LISTENING'):
            raise StreamSocket.NotListening
        
        ###print('accept: checking q for connection')
        new_sock, rhost, rport = self.conn_q.get(5) #check connection q for new conns
        ###print('accept: connection accepted')
        
        #set state
        new_sock.lock.acquire()
        new_sock.state = 'CONNECTED'
        new_sock.lock.release()

        return (new_sock, (rhost, rport)) #return conn
        

    def connect(self, addr):
        ###print(f"connect: self port - {self.port} arrived w/ address: {addr}")
        #exceptions
        if(self.state == 'CONNECTED'):
            raise StreamSocket.AlreadyConnected
        if(self.state == 'LISTENING'):
            raise StreamSocket.AlreadyListening
        
        #reset instance vars
        ###print('connect: waiting for sock lock')
        self.lock.acquire()
        ###print('connect: sock lock acquired')
        self.remote_addr = addr
        ###print('connect: sock lock released')
        

        #handle port not bound
        if(self.port == None):
            #ephem range - 49152 to 65535
            rand_port = random.randint(49152, 65535)
            while(self.proto.bound_ports.get(rand_port) != None):
                rand_port = random.randint(49152, 65535)
            self.port = rand_port
        
        ###print(f"connect: self port - {self.port}")
        #assemble SYN segment
        flags = 0 | 1 << 1 | 0 << 2
        self.seq_num = 0  # Initial sequence number
        #print(f"connect: seq_num - {self.seq_num}")
        self.lock.release()
        data_len = 0

        precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], self.seq_num, 0, flags, data_len)
        checksum = get_checksum(precheck)
        syn_seg = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], self.seq_num, 0, flags, data_len, checksum[0])

        #keep sending SYN until SYNACK is recieved
        segment = None
        while(segment == None):
            ###print('connect: sending SYN')
            ###print(f'connect: storing connection - ({self.port}, {addr[0]}, {addr[1]})')
            self.proto.connecting_socks[(self.proto.host.ip, self.port, addr[0], addr[1])] = self #store connection in connecting table
            
            self.lock.acquire()
            self.state = 'CONNECTING'
            self.lock.release()

            #wait for SYNACK
            try:
                self.output(syn_seg, addr[0])
                segment = self.seg_q.get(timeout=.001)
            except queue.Empty: 
                segment = None
                continue
            
            ###print("connect: Something put in q!")
            rport, _, seq_num, ack_num, flags, _, _= struct.unpack(HDR_FRMT, segment[:HDR_SIZE])
            
            #if not SYN ACK
            if(flags != 3):
                segment = None
                ###print("connect: incorrect response")
            else:
                x = 1
                #print("connect: SYNACK recieved!")
            

        #assemble ACK segment        
        flags = 1 | 0 << 1 | 0 << 2 #just doing this for myself lol
        data_len = 0

        self.lock.acquire()
        self.seq_num ^= 1
        self.last_recieved_num = 1 #need to set this for two way conn
        self.lock.release()
        #print(f"({self.port}connect: seq_num - {self.seq_num}")

        precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], self.seq_num, seq_num ^ 1, flags, data_len)
        checksum = get_checksum(precheck)
        ack_seg = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], self.seq_num, seq_num ^ 1, flags, data_len, checksum[0])

        #move from connecting to connected
        self.proto.lock.acquire()
        self.proto.connecting_socks.pop((self.proto.host.ip, self.port, addr[0], addr[1]))
        self.proto.connected_socks[(self.proto.host.ip, self.port, addr[0], addr[1])] = self
        self.proto.lock.release()

        self.state = "CONNECTED"

        #send ACK, if dropped -> server resends SYNACK -> client resends ACK
        self.output(ack_seg, addr[0])
        self.seq_num ^= 1
        #print(f'{self.port}client: conn established')
        return



    def send(self, data):
        '''
        stop and wait -> only finish the send once the ACK is recieved 
        resend after _ s
        '''

        ###print(f"({self.port}) send: arrived")
        
        #check if connected
        if(self.state != 'CONNECTED'):
            raise StreamSocket.NotConnected
        
        #create segment
        flags = 0
        precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], self.seq_num, 0, flags, len(data)) #TODO ACK and Seq nums
        checksum = get_checksum(precheck + data)
        hdr = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], self.seq_num, 0, flags, len(data), checksum[0])

        #send

        #wait for ack 
        ack_seg = None
        while ack_seg is None:
            try:
                #print(f"({self.port}) sending data")
                self.output(hdr+data, self.remote_addr[0])
                ack_seg = self.seg_q.get(timeout=0.001)
            except queue.Empty:
                #print(f"({self.port}) send: nothing in q - ACK or data must've dropped")
                ack_seg = None
                # loop again until we receive an ACK
                pass
        
        #print(f"({self.port}) send: ack recieved")
        self.seq_num ^= 1


    def handle_syn(self,seg, rhost):
        ###print("Handle Segment: arrived")
        rport, dport, seq_num, ack_num, flags, data_len, checksum = struct.unpack(HDR_FRMT, seg[:HDR_SIZE])
        ###print(f"Handle Segment: rport-{rport} dport-{dport} seq-{seq_num} ack-num{ack_num} flags-{flags} checksum-{checksum}")
        
        #handle the SYN case 
        ###print("Handle Segment: SYN Packet Recieved")

        #check if specified port is listening 
        dest_sock: RDTSocket = self.proto.bound_ports.get(dport)

        #raise exception if port isn't bound or isn't listening 
        if(dest_sock == None):
            ###print(f"Handle Segment: port {dport} is not bound")
            raise Exception
        
        ###print(f"Handle Segment: dest sock state: {dest_sock.state}")
        if(dest_sock.state != 'LISTENING'):
            raise StreamSocket.NotListening
        
        self.lock.acquire()
        self.last_recieved_num = seq_num
        #print(f"handle syn: Last rec - {self.last_recieved_num} ")
        self.seq_num = 0
        self.lock.release()

        #create new socket for conn
        new_sock: RDTSocket = self.proto.socket()
        new_sock.port = self.port
        new_sock.parent = self
        new_sock.remote_addr = (rhost, rport)
        new_sock.state = 'CONNECTING'

        #send SYN ACK
        flags = 1 | 1 << 1 | 0 << 2
        data_len = 0
        pre_check = struct.pack(PRECHK_HDR_FRMT, new_sock.port, rport, self.seq_num, seq_num ^ 1, flags, data_len)
        checksum = get_checksum(pre_check)
        synack_seg = struct.pack(HDR_FRMT, new_sock.port, rport, self.seq_num, seq_num ^ 1, flags, data_len, checksum[0])
        
        # Make sure the ACK event is cleared before starting
        self.ack_event.clear()
        
        # store the child socket in connecting_socks BEFORE starting resender
        self.proto.connecting_socks[(self.proto.host.ip, self.port, rhost, rport)] = new_sock

        # send first SYN-ACK immediately
        self.output(synack_seg, rhost)

        def synack_resender():
            while not self.ack_event.wait(timeout=.001):  # Wait up to 5 seconds for ACK
                # If event wasn't set (timeout occurred), resend
                self.output(synack_seg, rhost)

        # start as daemon so it won't block process exit
        t = threading.Thread(target=synack_resender, daemon=True)
        t.start()
        #print(f"({self.port})server: conn established")
        # return immediately (do NOT wait here)
        return
        

    def handle_data(self, seg, rhost):

        ###print("Handle Segment: arrived")
        rport, dport, seq_num, ack_num, flags, data_len, checksum = struct.unpack(HDR_FRMT, seg[:HDR_SIZE])
        #print(f"({self.port}) handle data: rec'd seq_num -{seq_num} last_recieved - {self.last_recieved_num} ")
        #if incorrect seq num then resend last ack 
        if(seq_num != self.last_recieved_num ^ 1):
            #print("handle data: incorrect seq num")
            #assemble ACK
            precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], 0, self.last_recieved_num ^ 1, 1, 0)
            checksum = get_checksum(precheck)
            seg = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], 0, self.last_recieved_num ^ 1, 1, 0, checksum[0])
            
            #send ACK
            self.output(seg, self.remote_addr[0])
            return
            
        
        #deliver data
        ###print(f"Handle Segment: rport-{rport} dport-{dport} seq-{seq_num} ack-num{ack_num} flags-{flags} checksum-{checksum}")
        #print("Handle Segment: Delivering data")
        self.deliver(seg[HDR_SIZE:])
        self.last_recieved_num = seq_num
        # assemble ACK
        ack_num = seq_num + 1
        data_len = 0
        flags = 1

        # PRECHK_HDR_FRMT layout is: srcport, dstport, seq, ack, flags, datalen
        precheck = struct.pack(PRECHK_HDR_FRMT, self.port, self.remote_addr[1], 0, ack_num, flags, data_len)
        checksum = get_checksum(precheck)
        seg = struct.pack(HDR_FRMT, self.port, self.remote_addr[1], 0, ack_num, flags, data_len, checksum[0])

        # send ACK
        #print("Handle Segment: Data delivered, sending ACK ")

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
            ###print("Proto Input: arrived")
            rport, dport, seq_num, ack_num, flags, data_len, checksum = struct.unpack(HDR_FRMT, seg[:HDR_SIZE])
            ###print(f"Proto Input: rport-{rport} dport-{dport} seq-{seq_num} ack-num{ack_num} flags-{flags} datalen-{data_len} checksum-{checksum}")
        except Exception as e: 
            ###print(f"Proto Input: err while unpacking segment - {e}")
            return
        
        # Prevent loopback: ignore packets sent by this same host+port
        if (rhost, rport) == (self.host.ip, dport):
            ###print(f"Proto Input: loopback detected on ({rhost}, {rport}) — dropping packet")
            return
        #err check
        if(not verify_checksum(seg)):
            ###print('wrong checksum')
            #drop packet
            return
        
        #check if SYN
        if(flags == 2):
            ###print("Proto Input: SYN Recieved")
            #demux to listening ports
            dest_sock = self.server_sockets.get(dport)
            if(dest_sock == None):
                raise RDTSocket.NotBound()
            dest_sock.handle_syn(seg, rhost)
            return
        #check if SYNACK 
        elif(flags == 3):
            ###print(f"Proto Input: SYNACK - looking for key ({dport}, {rhost}, {rport})")
            # First check if we're still connecting
            dest_sock = self.connecting_socks.get((self.host.ip, dport, rhost, rport))
            if dest_sock is not None:
                ###print(f'Proto Input: placing SYNACK on {dest_sock.port}\'s q ')
                dest_sock.seg_q.put(seg)
                return
                
            # If not in connecting_socks, check if we're already connected
            dest_sock = self.connected_socks.get((self.host.ip, dport, rhost, rport))
            if dest_sock is not None:
                # We already received SYNACK and sent ACK before, but server didn't get it
                # Resend the ACK
                flags = 1  # ACK flag
                data_len = 0
                precheck = struct.pack(PRECHK_HDR_FRMT, dport, rport, dest_sock.seq_num ^ 1, seq_num ^ 1, flags, data_len)
                checksum = get_checksum(precheck)
                ack_seg = struct.pack(HDR_FRMT, dport, rport, dest_sock.seq_num ^ 1, seq_num ^ 1, flags, data_len, checksum[0])
                dest_sock.output(ack_seg, rhost)
            return

        #check if handshake ACK 

        ###print(f"Proto Input: ACK - looking for key ({dport}, {rhost}, {rport})")
        ###print(f"Proto Input: Available connecting keys: {list(self.connecting_socks.keys())}")
        dest_sock: RDTSocket = self.connecting_socks.pop((self.host.ip, dport, rhost, rport), None)

        if(flags == 1 and dest_sock != None):
            ###print("Proto Input: handshake ACK found!")
            
            # Add to connected sockets before setting event
            self.connected_socks[(self.host.ip, dport, rhost, rport)] = dest_sock
            
            # Put on connection queue first
            dest_sock.parent.conn_q.put((dest_sock, rhost, rport))
            dest_sock.last_recieved_num = seq_num
            #print(f"proto input: last recieved dest sock - {dest_sock.last_recieved_num}")
            
            # Set the event last - this unblocks the handle_syn thread
            dest_sock.parent.ack_event.set()

        else: #data or data ACK recieved
            #check if there's a conn
            dest_sock: RDTSocket = self.connected_socks.get((self.host.ip, dport, rhost, rport))
            if(dest_sock == None):
                ###print(f"Proto Input: no connected tuple found for ({dport}, {rhost}, {rport}), flags={flags}")
                return
            if flags == 1: #ACK recieved, place on seg q
                dest_sock.seg_q.put(seg)
            else:
                #pass to socket
                #print(f"{dest_sock.port} Proto Input: handing off to socket")
                dest_sock.handle_data(seg, rhost)
            


                
##HELPERS##

def get_checksum(precheck: bytes) -> bytes:
    """Return 1-byte checksum as bytes object."""
    checksum_val = (~(sum(precheck) & 0xFF)) & 0xFF  # invert and mask to 1 byte
    ##print(f"checksum val: {checksum_val}")
    ##print(f"precheck no and: {sum(precheck)}")
    ##print(f"precheck sum: {sum(precheck) & 0xFF}")
    temp = (sum(precheck)& 0xFF ) + (~(sum(precheck)& 0xFF))
    ##print(f"temp: {temp}")
    ##print(f"0xFF: {0xFF}")
    return bytes([checksum_val])


def verify_checksum(segment: bytes) -> bool:
    """Verify simple 1-byte checksum."""
    total = sum(segment) & 0xFF
    ##print(f"total: {total}")
    return total == 0xFF