"""Cache-only member presentation of the authoritative game library."""
from collections import Counter
import config


def member_counts(guild, games):
    wanted = {g['role_id'] for g in games if g.get('role_id')}
    counts = Counter()
    # One pass through the gateway cache, not role.members once per game.
    for member in guild.members:
        counts.update({r.id for r in member.roles} & wanted)
    return {g['id']: counts[g.get('role_id')] for g in games}


def sections(games, counts):
    ordered = sorted(games, key=lambda g: (g['name'].casefold(), g['id']))
    result = {'🔥 Popular': sorted(ordered, key=lambda g: (-counts.get(g['id'], 0), g['name'].casefold(), g['id']))[:config.POPULAR_GAMES_COUNT]}
    for game in ordered:
        letter = game['name'][0].upper()
        if not 'A' <= letter <= 'Z':
            letter = '#'
        result.setdefault(letter, []).append(game)
    return {k: v for k, v in result.items() if v}
