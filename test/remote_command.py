#!/usr/bin/env python2.7

from test_base import connect_ssh_test

def ssh_cb(ssh):
    data = ssh.execute('lsb_release -a')
    print(data)

    data = ssh.execute('whoami')
    print(data)

connect_ssh_test(ssh_cb)

