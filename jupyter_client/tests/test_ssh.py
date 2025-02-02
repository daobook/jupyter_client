from jupyter_client.ssh.tunnel import select_random_ports


def test_random_ports():
    for _ in range(4096):
        ports = select_random_ports(10)
        assert len(ports) == 10
        for p in ports:
            assert ports.count(p) == 1
