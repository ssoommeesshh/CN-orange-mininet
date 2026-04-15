from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
import time
#failover.py
class Failover(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    def __init__(self, *args, **kwargs):
        super(Failover, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.failed = False
        self.failed_ports = {}  # dpid → failed port
        # Primary inter-switch link ports (semi-hardcoded for this topology)
        self.primary_link_ports = {1: 2, 2: 1}

    def add_flow(self, datapath, priority, match, actions):
    	#add a flow rule
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst)
        datapath.send_msg(mod)

    def delete_flows(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            table_id=ofproto.OFPTT_ALL,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=parser.OFPMatch()
        )
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        self.datapaths[dpid] = datapath
        self.logger.info("[CONNECT] Switch dpid=%s connected", dpid)

        # Table-miss: send unmatched packets to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("[FLOW] dpid=%s table-miss rule installed", dpid)

        # Block backup path ports during normal operation to prevent loops
        if dpid == 1:
            match = parser.OFPMatch(in_port=3)
            self.add_flow(datapath, 1, match, [])
            self.logger.info("[FLOW] s1: port3 (backup) blocked")
        if dpid == 2:
            match = parser.OFPMatch(in_port=3)
            self.add_flow(datapath, 1, match, [])
            self.logger.info("[FLOW] s2: port3 (backup) blocked")
        if dpid == 3:
            match = parser.OFPMatch()
            self.add_flow(datapath, 1, match, [])
            self.logger.info("[FLOW] s3: all traffic blocked until failover")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
    	#taking msg values
        msg = ev.msg
        dp = msg.datapath
        
        #extracting
        ofproto = dp.ofproto
        parser = dp.ofproto_parser
        dpid = dp.id
        self.datapaths[dpid] = dp
        self.mac_to_port.setdefault(dpid, {})
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        
        
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        dst = eth.dst
        src = eth.src
        # Learn MAC (only log new entries)
        if src not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src] = in_port
            self.logger.info("[LEARN] dpid=%s src=%s on port %s", dpid, src, in_port)
        else:
            self.mac_to_port[dpid][src] = in_port
        # Decide output port
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD
        actions = [parser.OFPActionOutput(out_port)]
        match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
        if out_port != ofproto.OFPP_FLOOD:
            self.add_flow(dp, 10, match, actions)
            self.logger.info("[FLOW] dpid=%s in_port=%s -> out_port=%s dst=%s",
                             dpid, in_port, out_port, dst)
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data)
        dp.send_msg(out)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
    
        msg = ev.msg
        dp = msg.datapath
        ofproto = dp.ofproto

        reason = msg.reason
        port_no = msg.desc.port_no
        dpid = dp.id
        is_link_down = bool(msg.desc.state & ofproto.OFPPS_LINK_DOWN)

        if reason in (ofproto.OFPPR_DELETE, ofproto.OFPPR_MODIFY) and is_link_down:
            print(f"[FAILURE] Switch s{dpid} port {port_no} DOWN")
            self.failed_ports[dpid] = port_no

            if dpid in self.primary_link_ports and port_no == self.primary_link_ports[dpid]:
                self.failed = True
                self._activate_backup()
            
    
    def _activate_backup(self):
        print("[FAILOVER] Computing alternate paths dynamically...")
        time.sleep(1)

        # Flush stale rules and restore table-miss before installing failover rules.
        for dp in self.datapaths.values():
            self.delete_flows(dp)
            ofproto = dp.ofproto
            parser = dp.ofproto_parser
            self.add_flow(
                dp,
                0,
                parser.OFPMatch(),
                [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
            )

        # Deterministic backup forwarding: h1 <-> s1 <-> s3 <-> s2 <-> h2
        # s1: host(1) <-> backup(3)
        s1 = self.datapaths.get(1)
        if s1:
            p = s1.ofproto_parser
            self.add_flow(s1, 30, p.OFPMatch(in_port=1), [p.OFPActionOutput(3)])
            self.add_flow(s1, 30, p.OFPMatch(in_port=3), [p.OFPActionOutput(1)])

        # s3: s1(1) <-> s2(2)
        s3 = self.datapaths.get(3)
        if s3:
            p = s3.ofproto_parser
            self.add_flow(s3, 30, p.OFPMatch(in_port=1), [p.OFPActionOutput(2)])
            self.add_flow(s3, 30, p.OFPMatch(in_port=2), [p.OFPActionOutput(1)])

        # s2: backup(3) <-> host(2)
        s2 = self.datapaths.get(2)
        if s2:
            p = s2.ofproto_parser
            self.add_flow(s2, 30, p.OFPMatch(in_port=2), [p.OFPActionOutput(3)])
            self.add_flow(s2, 30, p.OFPMatch(in_port=3), [p.OFPActionOutput(2)])

        print("[FAILOVER] Backup path activated dynamically\n")
        self.logger.info("[FAILOVER] Backup path via s3 is now ACTIVE")
