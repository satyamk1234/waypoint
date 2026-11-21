"""Normalize common country names/codes; never infer eligibility from 'remote'."""
import re
ALIASES = {}
for name, aliases in {
    'india':['in','ind','india'], 'united states':['us','usa','u.s.','united states','united states of america'],
    'united kingdom':['uk','gb','great britain','united kingdom'], 'canada':['ca','canada'],
    'australia':['au','australia'], 'germany':['de','germany'], 'france':['fr','france'],
    'singapore':['sg','singapore'], 'netherlands':['nl','netherlands'], 'ireland':['ie','ireland'],
    'spain':['es','spain'], 'brazil':['br','brazil'], 'poland':['pl','poland'],
    'pakistan':['pk','pakistan'], 'bangladesh':['bd','bangladesh'], 'japan':['jp','japan'],
    'new zealand':['nz','new zealand'], 'south africa':['za','south africa'],
    'united arab emirates':['ae','uae','united arab emirates'],
}.items():
    for alias in aliases: ALIASES[alias] = name

def country(value):
    value = ' '.join(value.strip().casefold().split())
    return ALIASES.get(value, value)

def country_tokens(values):
    return '|' + '|'.join(sorted({country(v).replace('|','') for v in values if v.strip()})) + '|'

def listed_eligibility(location):
    if location.strip().casefold() in {'worldwide','anywhere','global'}:
        return 'worldwide', []
    parts = [p.strip().casefold() for p in re.split(r'[,;/]',location)]
    known = [ALIASES[p] for p in parts if p in ALIASES]
    return ('countries', sorted(set(known))) if known else ('unknown', [])
