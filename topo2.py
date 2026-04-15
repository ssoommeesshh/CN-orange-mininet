from mininet.topo import Topo

class FailoverTopo2(Topo):
    def build(self):

        # Hosts
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        h3 = self.addHost('h3')

        # Switches
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

        # Extra host on backup path
        self.addLink(h3, s3)

topos = {'failovertopo2': (lambda: FailoverTopo2())}
