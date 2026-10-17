"""The reel as a whole: how its pictures run shot to shot (reel flow)."""
from __future__ import annotations


def register_commands(subparsers):
    parser = subparsers.add_parser('reel', help='The reel as a whole: picture flow against the reference')
    commands = parser.add_subparsers(dest='reel_command', required=True)
    p = commands.add_parser('flow', help='how the built pictures run shot to shot (studio/reel_flow.REEL_FLOW)')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: __import__('studio.reel_flow', fromlist=['built']).built(a.project))
    return commands
