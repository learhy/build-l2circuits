#!/usr/local/bin/python
# Imports
import warnings
import os
import sys
import paramiko
import argparse
import socket
from jnpr.junos.utils.config import Config
from jnpr.junos import Device
from jnpr.junos.exception import *
from distutils.util import strtobool
from lxml import etree
import lxml
import re
                                    
# Options
# Note: I'm ignoring the warning generated from PyEZ
paramiko.util.log_to_file("/tmp/build_l2circuit-paramiko.log")
warnings.filterwarnings("ignore")


# Functions


def resolvehostname(hostname):
    try:
        ipaddr=socket.gethostbyname(hostname)
        return ipaddr
    except socket.error:
        # Not legal
        print "didn't work"
    except Exception as err:
        print "nope"


# Check end point will make sure that the devices at each end of the 
# circuit are reachable.
def check_end_point (router, username, portnum):
    dev = Device(host=router, user=username, port=portnum)
    print "\t-connecting to %s..." % router,
    try:
        dev.open()
        print "success"
        dev.close()
        return True
    except Exception as err:
        print "\tCannot connect to %s: " % router, err


def set_end_point(router_a, a_ifc, router_z, z_ifc, bw, vcid, username, portnum):
    # local variables
    lsp_shortname = "test_circuit_" + router_z + "_from_" + router_a
    tvars = {
    'lsp_name' : lsp_shortname, 
    'lsp_egress' : router_z,
    'lsp_bandwidth' : bw, 
    'l2circuit_ingress_ifc' : a_ifc,
    'vcid' : vcid }
    dev = Device(host=router_a, user=username, port=portnum)
    
    # Attempt to bind to the endpoint
    try:
        dev.open()
    except Exception as err:
        print "Cannot connect to device:", err
        # sys.exit?q
    
    # Bind a configuration unit to the device
    dev.bind( cu=Config )
    
    # Attempt a lock on the configuration
    print "{}:\tLocking the configuration".format(router_a)
    try:
        dev.cu.lock()
    except LockError:
        print "Error: Unable to lock configuration on ", router_a
        dev.close()
        # sys.exit needs to go here
    
    # Load the configuration into candidate config
    print "{}:\tLoading configuration changes as candidate configs".format(router_a)
    try:
        dev.cu.load(template_path='layer2_circuit_config_template.conf', 
            merge=True, template_vars=tvars, format="text")
    except ValueError as err:
        print err.message
        print "debugging-- ValueError"    
    except Exception as err:
        print err #helpful debugging for config errors?
        print err.rsp.findtext('.//error-message') 
        if err.rsp.find('.//ok') is None:
            rpc_msg = err.rsp.findtext('.//error-message')
            #rpc_msg = etree.tostring(err.rsp) # helpful for debugging
            print "Unable to load configuration changes: ", rpc_msg
    
        print "{}:\tUnlocking the configuration".format(router_a)
        try:
            dev.cu.unlock()
        except UnlockError:
            print "Error: Unable to unlock {} configuration".format(router_a)
        dev.close()

    print "{}:\tCommitting the configuration".format(router_a)
    try:
        dev.cu.commit()
    except CommitError:
        print "Error: Unable to commit configuration"
        print "Unlocking the configuration"
        try:
            dev.cu.unlock()
        except UnlockError:
            print "Error: Unable to unlock configuration"
    dev.close()


def get_vlan_tag (router_a, username, portnum, ifc):
    # Return the vlan tag configured on the service provider port (routerZ)
    dev = Device(host=router_a, user=username, port=portnum)
    try:
        dev.open()
    except Exception as err:
        print "Cannot connect to {}. Error: {}".format(router_a, err)
        sys.exit()    
    interface_info = dev.rpc.get_interface_information(interface_name=ifc)
    vlan = interface_info.findtext('logical-interface/link-address')
    # use a regex to grab the vlan tag in a list form
    tag = re.findall(r"(?<=\[ 0x8100.)\d+", vlan)
    return tag[0]


def set_vlan_id(router_a, a_ifc, vlanid):
    print ("\nConfiguring headend router {} ifc {} to match tail vlan-id (vlan-id: {})"
        .format(router_a, a_ifc, vlanid)) 
    dev = Device(host=router_a)
    cfg = Config(dev)
    try:
        dev.open()
    except Exception as err:
        print "Cannot connect to {}. Error: {}".format(router_a, err)
        sys.exit()  
    # Attempt a lock on the configuration
    
    try:
        cfg.lock()
        print "{}:\tLocking the configuration".format(router_a)
    except LockError:
        print "Error: Unable to lock configuration on ", router_a
        dev.close()
        sys.exit("Aborting.")
    print "{}:\tLoading configuration changes as candidate configs".format(router_a)
    try:
        cfg.load("set interfaces {} vlan-id {}".
            format(a_ifc, vlanid), format="set", merge=True)
    except ValueError as err:
        print err.message
        print etree.dump(err.cmd)
        print etree.dump(err.rsp) 
        print "debugging-- ValueError"
        sys.exit("Aborting.")
        
    except Exception as err:
        print err #helpful debugging for config errors?
        print err.rsp.findtext('.//error-message') 
        if err.rsp.find('.//ok') is None:
            rpc_msg = err.rsp.findtext('.//error-message')
            rpc_msg = etree.tostring(err.rsp) # helpful for debugging
            print "Unable to load configuration changes: ", rpc_msg
            sys.exit("Aborting.")
    
        print "{}:\tUnlocking the configuration".format(router_a)
        try:
            cfg.unlock()
        except UnlockError:
            print "Error: Unable to unlock {} configuration".format(router_a)
            sys.exit("Aborting.")
        dev.close()
        
    print "{}:\tCommitting the configuration".format(router_a)
    try:
        cfg.commit()
#     except CommitError:
    except Exception as err:
        print "Error: Unable to commit configuration"
        print "Unlocking the configuration"
        print err.message
        print etree.dump(err.cmd)
        print etree.dump(err.rsp)
        try:
            cfg.unlock()
        except UnlockError:
            print "Error: Unable to unlock configuration"
    dev.close()


def tear_l2_circuit(router_a, a_ifc, router_z, z_ifc):
    dev = Device(host=router_a)
    lsp_shortname = "test_circuit_" + router_z + "_from_" + router_a
    print "Tearing down circuit"
    try:
        dev.open()
        print "{}:\tOpening connection".format(router_a)
    except Exception as err:
        sys.exit("Failed to open device: {}".format(err))
    try:
        print "{}:\tBinding configuration".format(router_a)
        cfg = Config(dev)
    except Exception as err:
        sys.exit("Failed to bind the configuration: {}".format(err))
    try:
        print "{}:\tLocking configuration".format(router_a)
        cfg.lock()
    except Exception as err:
        print err.rsp.findtext('.//error-message')
        sys.exit("Failed to lock the configuration: {}".format(err))
    try:
        print ("{}:\tRunning \'delete protocols mpls label-switched-path {}\'"
            .format(router_a, lsp_shortname))
        cfg.load("delete protocols mpls label-switched-path {}"
            .format(lsp_shortname), format="set")
        print ("{}:\tRunning \'delete protocols neighbor {}\'"
            .format(router_a, router_z))
        cfg.load("delete protocols l2circuit neighbor {}".format(router_z), format="set")
    except Exception as err:
        etree.dump(err.cmd)
        etree.dump(err.rsp)
        sys.exit("Could not load the configuration on {}: {}"
            .format(router_a, err.rsp.findtext('.//error-message')))
    try:
        print "{}:\tCommitting the configuration".format(router_a),
        cfg.commit()
        print " Success!"
    except Exception as err:
        print err.rsp.findtext('.//error-message')
        sys.exit("Could not commit the configuration on {}".format(router_a))

    
# "Main"
if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description='Utility to build and/or tear L2 pseudowires across K-20 on demand.')
    group = parser.add_mutually_exclusive_group(required=True)
    parser.add_argument('routerA',
        help='IP address (loopback) PE headend "A" of the circuit to be built')
    parser.add_argument('routerAifc', 
        help='interface of routerA, including vlan, if required (xe-X/Y/Z.###)')
    parser.add_argument('routerZ',
        help='IP address (loopback) PE headend "Z" of the circuit to be built')
    parser.add_argument('routerZifc', 
        help='interface of routerZ, including vlan, if required (xe-X/Y/Z.###)')
    parser.add_argument('bandwidth', help='Bandwidth in Mb or Gb')
    parser.add_argument('vcid', help='Virtual Circuit ID to use in the config')
    parser.add_argument('--username', 
        help='Username to connect to system with if different from  $USER env')
    parser.add_argument('--port', type=int, 
        help='Specify if NETCONF is running on a non-standard port (default is 22)')
    group.add_argument('--build', dest='build', action='store_true',
        help='Builds the specified circuit (default)')
    group.add_argument('--tear', dest='tear', action='store_true', 
        help='Tears down the specified circuit')
    args = parser.parse_args()
    
    portnum= args.port
    if not portnum:
        portnum = 22
    username= args.username
    if not username:
        username = os.environ['USER']
    routerA = args.routerA
    routerAifc = args.routerAifc
    routerZ = args.routerZ
    routerZifc = args.routerZifc
    bandwidth = args.bandwidth
    localvcid = args.vcid
    lsp_shortname = routerZ + "_from_" + routerA
    build=args.build
    tear=args.tear

    if build:
        if str(routerA) == str(routerZ):
            sys.exit("Endpoints cannot be for the same router- Why not a vlan? Aborting.")
        if resolvehostname(routerA) == resolvehostname(routerZ):
            sys.exit("Endpoints cannot be for the same router. Aborting.")

        # Convert the bandwidth to bits per second as required by the junos 
        # and validate input a bit

        if str(bandwidth[-1]).lower() == 'g':
            bps = int(bandwidth[:-1]) * int(1000000000)
        elif str(bandwidth[-1]).lower() == "m":
                bps = int(bandwidth[:-1]) * int(1000000)
        else:
            sys.exit("Bandwidth must be specified in Mb/s (M) or Gb/s (G)." 
                " You entered %s" %bandwidth)
        if bps > 10000000000:
            sys.exit("Layer 2 circuits in excess of 10Gb/s should not be " 
                "provisioned using this utility.")

        # Do stuff
        print ("This script will attempt to build a layer 2 circuit with the following "
            "criteria:")
        print "\tEndpoint A:\t\t{}:{}".format(routerA, routerAifc)
        print "\tEndpoint Z:\t\t{}:{}".format(routerZ, routerZifc)
        print "\tBandwidth:\t\t{}bps ({}b/s)".format(bps, bandwidth.upper())
        print "\tVirtual Circuit ID:\t{}".format(localvcid)

        # Prompt for user input
        input = raw_input("\nDo you wish to continue? (yes/no):")

        try:
            if strtobool(input) == True:
                print "Testing connectivity to endpoints..."
                for router in routerA, routerZ:
                    try:
                        if check_end_point(router, username, portnum):
                            continue
                        else: 
                            print ("One or more endpoints could not be contacted."
                                "Aborting")
                            sys.exit()
                    except Exception as err:
                        print err.message
            else:
                sys.exit("Aborted by user")
        except ValueError:
            sys.exit("Please enter yes or no. Aborting.")
    
        print "\nRetrieving vlan-id from the service provider port...",
        try:
            vlanID = (get_vlan_tag(resolvehostname(routerZ), 
                username, portnum, routerZifc))
            print "Success. Vlan ID is {}".format(vlanID)
        except Exception as err:
            sys.exit("Could not retreive vlan ID from {}. " 
                "Is vlan-tagging enabled on the port?".format(routerZ))
    
        print ("\nConfiguring l2circuit and RSVP reservation" 
            "on headend router: {}".format(routerA))
        try:
            set_end_point(resolvehostname(routerA), routerAifc, resolvehostname(routerZ),
                routerZifc, str(bps), localvcid, username, portnum)
        except Exception as err:
            print err.message
        print ("\nConfiguring l2circuit and RSVP reservation on tailend router {}"
            .format(routerZ))
        try:
            set_end_point(resolvehostname(routerZ), routerZifc, resolvehostname(routerA),
                routerAifc, str(bps), localvcid, username, portnum)
        except Exception as err:
            print err.message
        try:
            set_vlan_id(resolvehostname(routerA), routerAifc, vlanID)
        except Exception as err:
                print "Could not set vlan id: {}".format(err)
    elif tear:
        tear_l2_circuit(resolvehostname(routerA), routerAifc, resolvehostname(routerZ),\
         routerZifc)
        tear_l2_circuit(resolvehostname(routerZ), routerZifc, resolvehostname(routerA), \
        routerAifc)
    else:
        sys.exit("Must select either --build or --tear")
    