#!/usr/bin/env python3

import sys
import os.path
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))

import threading
import unittest
import random
import itertools
from network import *
from rdt import *
from exthread import *

class BaseNetworkTest(unittest.TestCase):
    """Base class to simplify setting up network, sockets, and connections"""
    # Protocol to use
    PROTO = RDTProtocol
    # Provide defaults
    LOSS = 0.00
    # List of client socket addresses (bound if port is not None)
    CLIENTS = []
    # List of listening socket addresses (port must be set)
    LISTEN = []
    # Map of connection names to (client index, server index) pairs
    CONNS = {}

    def _connect_clients(self, conns, tick):
        for n, (c, l) in conns.items():
            if c is None or l is None:
                continue
            tick.acquire()
            self.c[n].connect(type(self).LISTEN[l])

    def makeconns(self, conns):
        """Connect the sockets specified in conns, populating the s/l/c maps"""
        caddrs = type(self).CLIENTS
        laddrs = type(self).LISTEN
        tick = threading.Semaphore(value=0)
        with ExThread(target=self._connect_clients, args=(conns, tick)) as cthr:
            for n, (c, l) in conns.items():
                if l is not None:
                    self.l[n] = self.lsocks[laddrs[l]]
                if c is not None:
                    self.c[n] = self.csocks[c]
                if c is None or l is None:
                    continue
                tick.release()
                s, _ = self.l[n].accept()
                self.s[n] = s

    def setUp(self, conns=None):
        caddrs = type(self).CLIENTS
        laddrs = type(self).LISTEN
        if conns is None:
            conns = type(self).CONNS
        pid = type(self).PROTO.getid()

        # Create network and hosts
        n = Network(loss=type(self).LOSS)
        self.h = {}
        # Use set comprehension to eliminate duplicates
        for ip in {fst for fst, _ in itertools.chain(caddrs, laddrs)}:
            h = Host(n, ip)
            h.register_protocol(type(self).PROTO)
            self.h[ip] = h

        # Create and set up listening sockets
        self.lsocks = {}
        for ip, port in laddrs:
            ls = self.h[ip].socket(pid)
            ls.bind(port)
            ls.listen()
            self.lsocks[ip, port] = ls

        # Create client sockets
        self.csocks = []
        for ip, port in caddrs:
            cs = self.h[ip].socket(pid)
            if port:
                cs.bind(port)
            self.csocks.append(cs)

        # Set up connections
        self.s = {}
        self.l = {}
        self.c = {}
        self.makeconns(conns)

class A_LosslessConnectTest(BaseNetworkTest):
    CLIENTS = [('10.50.254.1', None), ('10.50.254.1', None),
               ('10.50.254.2', None), ('10.50.254.2', None)]
    LISTEN = [('10.50.254.2', 3324), ('10.50.254.2', 26232)]
    CONNS = {'a': (0, None), 'b': (1, None), 'c': (2, None), 'd': (3, None)}

    def test_00_bind_namespace(self):
        """Different hosts can bind the same port number"""
        self.c['a'].bind(42533)
        self.c['c'].bind(42533)

    def test_01_bindinuse(self):
        """Bind can raise AddressInUse"""
        self.c['a'].bind(29539)
        with self.assertRaises(Socket.AddressInUse):
            self.c['b'].bind(29539)

    def test_02_bindinuse_listen(self):
        """Bind can raise AddressInUse for listening socket"""
        with self.assertRaises(Socket.AddressInUse):
            self.c['d'].bind(type(self).LISTEN[0][1])

    def test_03_bindafteraccept(self):
        """Bind cannot be called after accept"""
        self.makeconns({'x': (0, 0)})
        with self.assertRaises(StreamSocket.AlreadyConnected):
            self.s['x'].bind(23583)

    def test_04_bindafterconnect(self):
        """Bind cannot be called after connect"""
        self.makeconns({'x': (0, 0)})
        with self.assertRaises(StreamSocket.AlreadyConnected):
            self.c['x'].bind(29691)

    def test_05_listenafterconnect(self):
        """Listen cannot be called after connect"""
        self.makeconns({'x': (0, 0)})
        with self.assertRaises(StreamSocket.AlreadyConnected):
            self.c['x'].listen()

    def test_06_2connect(self):
        """Connect cannot be called twice on a socket"""
        self.makeconns({'x': (0, 0)})
        with self.assertRaises(StreamSocket.AlreadyConnected):
            self.c['x'].connect(type(self).LISTEN[1])

    def test_07_notbound(self):
        """Bind must be called before listen"""
        with self.assertRaises(StreamSocket.NotBound):
            self.c['d'].listen()

    def test_08_notlistening(self):
        """Listen must be called before accept"""
        self.c['c'].bind(13579)
        with self.assertRaises(StreamSocket.NotListening):
            self.c['c'].accept()

    def test_09_notconnected(self):
        """Socket must be connected to send"""
        self.c['c'].bind(9532)
        with self.assertRaises(StreamSocket.NotConnected):
            self.c['c'].send(b'test-notconnected')

class B_Lossless_1x1(BaseNetworkTest):
    CLIENTS = [('192.168.10.1', None), ('192.168.10.2', None)]
    LISTEN = [('192.168.10.1', 26093), ('192.168.10.2', 2531)]
    CONNS = {'a': (0, None), 'b': (1, None)}

    def client_bindsrc(self):
        self.c['a'].bind(32901)
        self.c['a'].connect((type(self).CLIENTS[1][0], 9920))

    def test_00_bindsrc(self):
        """Binding a client socket sets the source address"""

        self.c['b'].bind(9920)
        self.c['b'].listen()
        with ExThread(target=self.client_bindsrc) as client:
            cs, (host, port) = self.c['b'].accept()
            self.assertEqual(host, type(self).CLIENTS[0][0])
            self.assertEqual(port, 32901)

    def test_01_oneway(self):
        """Data can be sent in one direction"""
        self.makeconns({'c': (0, 1)})
        self.c['c'].send(b'test-oneway')
        self.assertEqual(self.s['c'].recv(), b'test-oneway')

    def test_02_otherway(self):
        """Data can be sent in the other direction"""
        self.makeconns({'c': (1, 0)})
        self.c['c'].send(b'test-otherway')
        self.assertEqual(self.s['c'].recv(), b'test-otherway')

    def test_03_oneway_pcs(self):
        """Multiple pieces of data are made into one stream"""
        self.makeconns({'c': (0, 1)})
        self.c['c'].send(b'test-onew')
        self.c['c'].send(b'')
        self.c['c'].send(b'ay-pcs')
        self.assertEqual(self.s['c'].recv(), b'test-oneway-pcs')

    def test_04_twoway(self):
        """Data can be sent both ways over a connected socket"""
        self.makeconns({'c': (0, 1)})
        self.c['c'].send(b'test-twoway1')
        self.assertEqual(self.s['c'].recv(), b'test-twoway1')
        self.s['c'].send(b'test-twoway2')
        self.assertEqual(self.c['c'].recv(), b'test-twoway2')

    def client_stress(self, MIN, MAX, TOTAL, data):
        # Verify that the data comes in in the right amount/order
        count = 0
        while count < TOTAL - MAX:
            incoming = self.s['c'].recv(MAX)
            b = len(incoming)
            ofs = count % MAX
            self.assertEqual(incoming, data[ofs:ofs+b])
            count += b

    def test_05_stress(self):
        """A lot of data can be sent and received"""
        self.makeconns({'c': (0, 1)})
        # Send 8 MB in random sizes of up to 1400 B
        MIN, MAX, TOTAL = 1, 1400, int(8e6)
        data = b'0123456789' * (MAX // 5)
        with ExThread(target=self.client_stress, args=(MIN, MAX, TOTAL, data)) as cthr:
            count = 0
            while count < TOTAL - MAX:
                b = random.randint(MIN, MAX)
                ofs = count % MAX
                self.c['c'].send(data[ofs:ofs+b])
                count += b
            b = TOTAL - count
            ofs = count % MAX
            self.c['c'].send(data[ofs:ofs+b])

class C_Lossless_SameHost(B_Lossless_1x1):
    """Runs the Lossless 1x1 tests between two sockets on a single host"""
    CLIENTS = [('92.68.10.1', None), ('92.68.10.1', None)]

def _serversock(sock, port):
    sock.bind(port)
    sock.listen()
    return sock

class D_Lossless_1x2(BaseNetworkTest):
    CLIENTS = [('172.16.170.22', None), ('172.16.170.22', None)]
    LISTEN = [('172.16.170.111', 20956), ('172.16.170.3', 1255)]
    CONNS = {'a': (0, 0), 'b': (1, 1)}

    def test_00_mux(self):
        """Multiple client sockets can coexist on a host"""
        self.c['a'].send(b'2test-')
        self.c['b'].send(b'_te')
        self.c['b'].send(b'st3-3mux_')
        self.c['a'].send(b'mux2')
        self.assertEqual(self.s['b'].recv(), b'_test3-3mux_')
        self.assertEqual(self.s['a'].recv(), b'2test-mux2')

        self.c['a'].send(b'test2/mux2')
        self.c['b'].send(b'///test-mux///3')
        self.assertEqual(self.s['b'].recv(), b'///test-mux///3')
        self.assertEqual(self.s['a'].recv(), b'test2/mux2')

class E_Lossless_1x2_SameHost(D_Lossless_1x2):
    """Runs the Lossless 1x2 tests between three sockets on a single host"""
    CLIENTS = [('16.24.32.48', None), ('16.24.32.48', None)]
    LISTEN = [('16.24.32.48', 10956), ('16.24.32.48', 11255)]

class F_Lossless_2x1(BaseNetworkTest):
    CLIENTS = [('172.16.22.1', None), ('172.16.22.2', None)]
    LISTEN = [('172.16.22.3', 20063)]
    CONNS = {'a': (0, 0), 'b': (1, 0)}

    def test_00_oneway(self):
        self.c['a'].send(b'aaa-test2x1oneway-aaa')
        self.c['b'].send(b'bbb-test2x1oneway-bbb')
        self.assertEqual(self.s['a'].recv(), b'aaa-test2x1oneway-aaa')
        self.assertEqual(self.s['b'].recv(), b'bbb-test2x1oneway-bbb')
        self.c['a'].send(b'AAA-TEST2X1ONEWAY-AAA')
        self.c['b'].send(b'BBB-TEST2X1ONEWAY-BBB')
        self.assertEqual(self.s['b'].recv(), b'BBB-TEST2X1ONEWAY-BBB')
        self.assertEqual(self.s['a'].recv(), b'AAA-TEST2X1ONEWAY-AAA')

    def test_01_twoway(self):
        self.c['a'].send(b'aaa-test2x1twoway-aaa')
        self.c['b'].send(b'bbb-test2x1twoway-bbb')
        self.s['a'].send(b'twoway_aaa_2x1')
        self.s['b'].send(b'twoway_bbb_2x1')
        self.assertEqual(self.s['a'].recv(), b'aaa-test2x1twoway-aaa')
        self.assertEqual(self.c['a'].recv(), b'twoway_aaa_2x1')
        self.assertEqual(self.s['b'].recv(), b'bbb-test2x1twoway-bbb')
        self.assertEqual(self.c['b'].recv(), b'twoway_bbb_2x1')

        self.c['a'].send(b'aaa-test2x1twoway-aaa')
        self.s['b'].send(b'twoway_bbb_2x1')
        self.s['a'].send(b'twoway_aaa_2x1')
        self.c['b'].send(b'bbb-test2x1twoway-bbb')
        self.assertEqual(self.s['b'].recv(), b'bbb-test2x1twoway-bbb')
        self.assertEqual(self.c['b'].recv(), b'twoway_bbb_2x1')
        self.assertEqual(self.c['a'].recv(), b'twoway_aaa_2x1')
        self.assertEqual(self.s['a'].recv(), b'aaa-test2x1twoway-aaa')

class G_Lossless_2x1_SameHost(F_Lossless_2x1):
    CLIENTS = [('8.8.4.4', None), ('8.8.4.4', None)]
    LISTEN = [('8.8.4.4', 20063)]

class H_Lose10_1x1(BaseNetworkTest):
    LOSS = 0.10
    CLIENTS = [('192.168.40.{}'.format(x), None) for x in range(100)]
    LISTEN = [('192.168.50.{}'.format(x), 36000 + x) for x in range(100)]
    CONNS = {x: (x, None) for x in range(100)}

    def test_00_connectall(self):
        self.makeconns({x: (x, x) for x in range(100)})

if __name__ == '__main__':
    unittest.main()
