#!/usr/bin/python

import logging

from pysecure import log_config

#ssh_get_pubkey_hash, \
#ssh_get_hexa, \
#free, \
#strerror, 
#ssh_get_error, \
from pysecure.adapters.ssha import ssh_is_server_known, \
                                   ssh_write_knownhost, \
                                   ssh_userauth_privatekey_file, SshSession, \
                                   SshConnect, SshSystem, PublicKeyHash

from pysecure.adapters.sftpa import SftpSession, sftp_listdir

user = 'dustin'
host = 'dustinplex'
key_filepath = '/home/dustin/.ssh/id_dsa'
verbosity = 0

with SshSystem():
    with SshSession(user=user, host=host, verbosity=verbosity) as session:
        with SshConnect(session):
            logging.debug("Ready to authenticate.")

            def hostkey_gate(hk, would_accept):
                logging.debug("CB HK: %s" % (hk))
                logging.debug("CB Would Accept: %s" % (would_accept))
                
                return would_accept

            ssh_is_server_known(session, cb=hostkey_gate)
            ssh_userauth_privatekey_file(session, None, key_filepath, None)

            with SftpSession(session) as sftp:
                print("Name                         Size Perms    Owner\tGroup\n")
                for attributes in sftp_listdir(sftp, '.'):
                    print("%-40s %10d %.8o %s(%d)\t%s(%d)" % 
                          (attributes.name[0:40], attributes.size, 
                           attributes.permissions, attributes.owner, 
                           attributes.uid, attributes.group, attributes.gid))

