"""Read-only DNS and public TLS checks before the staging Web route is opened."""

import argparse
import http.client
import json
import re
import socket
import ssl


IPV4 = '74.91.132.250'
CALLBACK = '/api/auth/discord/callback'


class EdgeCheckError(Exception):
    pass


def verify_dns(hostname):
    if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}', hostname):
        raise EdgeCheckError('INVALID_STAGING_HOSTNAME')
    try:
        addresses = {result[4][0] for result in socket.getaddrinfo(hostname + '.', 443, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        raise EdgeCheckError('STAGING_DNS_UNAVAILABLE') from None
    if addresses != {IPV4}:
        raise EdgeCheckError('STAGING_DNS_ADDRESS_MISMATCH')


def probe(hostname, *, tls):
    port = 443 if tls else 80
    # Dial the approved address after checking DNS. Use the selected hostname
    # for certificate verification, SNI and the HTTP Host header.
    with socket.create_connection((IPV4, port), timeout=8) as raw:
        connection = http.client.HTTPConnection(hostname, port, timeout=8)
        try:
            connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=hostname) if tls else raw
            connection.request('GET', '/', headers={'Host': hostname})
            with connection.getresponse() as response:
                return (response.status, response.getheader('Location'), response.getheader('X-GamerHQ-Staging-Edge'),
                        response.getheader('Cache-Control'))
        finally:
            connection.close()


def preflight(hostname, *, dns_only=False):
    verify_dns(hostname)
    origin = 'https://' + hostname
    if not dns_only:
        status, location, _, _ = probe(hostname, tls=False)
        if status not in {301, 308} or location != origin + '/':
            raise EdgeCheckError('STAGING_HTTPS_REDIRECT_MISMATCH')
        status, _, marker, caching = probe(hostname, tls=True)
        if (status, marker, caching) != (503, 'parked', 'no-store'):
            raise EdgeCheckError('STAGING_PARKED_EDGE_MISMATCH')
    return {'state': 'DNS_VERIFIED' if dns_only else 'PARKED_HTTPS_VERIFIED',
            'hostname': hostname, 'ipv4': IPV4, 'discordRedirectUri': origin + CALLBACK}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hostname')
    parser.add_argument('--dns-only', action='store_true')
    args = parser.parse_args()
    try:
        result = preflight(args.hostname, dns_only=args.dns_only)
    except EdgeCheckError as error:
        raise SystemExit(str(error)) from None
    except (OSError, http.client.HTTPException):
        raise SystemExit('STAGING_EDGE_UNREACHABLE_OR_UNTRUSTED') from None
    print(json.dumps(result))


if __name__ == '__main__':
    main()
