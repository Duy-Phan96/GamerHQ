"""Print the reviewed guild install link for a GamerHQ Discord application."""
import argparse

from services.discord_permissions import core_install_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application_id', type=int)
    args = parser.parse_args()
    if not 0 < args.application_id < 2**64:
        parser.error('application_id must be a Discord snowflake')
    print(core_install_url(args.application_id))


if __name__ == '__main__':
    main()
