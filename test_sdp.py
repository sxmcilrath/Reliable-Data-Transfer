#!/usr/bin/env python3

from network import *
from sdp import *

import unittest

class A_SDPTest(unittest.TestCase):
    def setUp(self):
        self.n = Network()
        self.h1 = Host(self.n, '192.168.10.1')
        self.h2 = Host(self.n, '192.168.10.2')
        self.h1.register_protocol(SampleDatagramProtocol)
        self.h2.register_protocol(SampleDatagramProtocol)
        self.s1 = self.h1.socket(99)
        self.s2 = self.h2.socket(99)

    def test_oneway(self):
        self.s1.sendto(b'hello', '192.168.10.2')
        self.s1.sendto(b'', '192.168.10.2')
        self.s1.sendto(b' world', '192.168.10.2')

        self.assertEqual(self.s2.recvfrom(), (b'hello', '192.168.10.1'))
        self.assertEqual(self.s2.recvfrom(), (b'', '192.168.10.1'))
        self.assertEqual(self.s2.recvfrom(), (b' world', '192.168.10.1'))

if __name__ == '__main__':
    unittest.main()
