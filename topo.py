from mininet.topo import Topo


# TOPOLOGY 1 (Basic Failover)

class FailoverTopo(Topo):
    def build(self):

        h1 = self.addHost('h1', ip='10.0.0.1/24')
        h2 = self.addHost('h2', ip='10.0.0.2/24')

        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')

        # Primary path
        self.addLink(h1, s1)
        self.addLink(s1, s2)
        self.addLink(s2, h2)

        # Backup path
        self.addLink(s1, s3)
        self.addLink(s3, s2)



topos = {'failovertopo': (lambda: FailoverTopo())}
