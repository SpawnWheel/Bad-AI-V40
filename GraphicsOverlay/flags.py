"""Country flags for the graphics: PNGs from flagcdn.com (Wikimedia's public-domain flags), saved in
Graphics/Flags under the 3-letter code used on the Drivers tab (GBR.png, RSA.png, ...).

python -m GraphicsOverlay.flags          downloads every flag listed below
"""
import os
import sys
import urllib.request

from . import config as gcfg

SOURCE = 'https://flagcdn.com/w160/{}.png'

# 3-letter code (ISO 3166 and the sporting/IOC spellings) -> flagcdn code
CODES = {
    'ARG': 'ar', 'AUS': 'au', 'AUT': 'at', 'BEL': 'be', 'BRA': 'br', 'BGR': 'bg', 'BUL': 'bg', 'CAN': 'ca',
    'CHL': 'cl', 'CHI': 'cl', 'CHN': 'cn', 'COL': 'co', 'HRV': 'hr', 'CRO': 'hr', 'CZE': 'cz', 'DNK': 'dk',
    'DEN': 'dk', 'EST': 'ee', 'FIN': 'fi', 'FRA': 'fr', 'DEU': 'de', 'GER': 'de', 'GRC': 'gr', 'GRE': 'gr',
    'HUN': 'hu', 'ISL': 'is', 'IND': 'in', 'IDN': 'id', 'INA': 'id', 'IRL': 'ie', 'ISR': 'il', 'ITA': 'it',
    'JPN': 'jp', 'KOR': 'kr', 'LVA': 'lv', 'LAT': 'lv', 'LTU': 'lt', 'LUX': 'lu', 'MYS': 'my', 'MAS': 'my',
    'MLT': 'mt', 'MEX': 'mx', 'MCO': 'mc', 'MON': 'mc', 'NLD': 'nl', 'NED': 'nl', 'NZL': 'nz', 'NOR': 'no',
    'PER': 'pe', 'PHL': 'ph', 'PHI': 'ph', 'POL': 'pl', 'PRT': 'pt', 'POR': 'pt', 'ROU': 'ro', 'RUS': 'ru',
    'SAU': 'sa', 'KSA': 'sa', 'SRB': 'rs', 'SGP': 'sg', 'SVK': 'sk', 'SVN': 'si', 'SLO': 'si', 'ZAF': 'za',
    'RSA': 'za', 'ESP': 'es', 'SWE': 'se', 'CHE': 'ch', 'SUI': 'ch', 'THA': 'th', 'TUR': 'tr', 'UKR': 'ua',
    'ARE': 'ae', 'UAE': 'ae', 'GBR': 'gb', 'UK': 'gb', 'USA': 'us', 'URY': 'uy', 'URU': 'uy', 'VEN': 've',
    'ZWE': 'zw', 'ZIM': 'zw', 'NAM': 'na', 'KEN': 'ke', 'NGA': 'ng', 'EGY': 'eg', 'MAR': 'ma', 'MOR': 'ma',
    'BWA': 'bw', 'BOT': 'bw', 'MOZ': 'mz', 'ZMB': 'zm', 'ZAM': 'zm', 'QAT': 'qa', 'BHR': 'bh', 'BRN': 'bh',
    'HKG': 'hk', 'TWN': 'tw', 'TPE': 'tw', 'VNM': 'vn', 'VIE': 'vn', 'PAK': 'pk', 'ECU': 'ec', 'CRI': 'cr',
    'CRC': 'cr', 'PRY': 'py', 'PAR': 'py', 'BOL': 'bo', 'CYP': 'cy', 'GEO': 'ge', 'BLR': 'by', 'AND': 'ad',
    'LIE': 'li', 'SMR': 'sm', 'BIH': 'ba', 'MNE': 'me', 'MKD': 'mk', 'ALB': 'al', 'KAZ': 'kz', 'AZE': 'az',
    'ARM': 'am', 'MDA': 'md', 'JAM': 'jm', 'PRI': 'pr', 'PUR': 'pr', 'DOM': 'do', 'CUB': 'cu',
    'ENG': 'gb-eng', 'SCO': 'gb-sct', 'SCT': 'gb-sct', 'WAL': 'gb-wls', 'WLS': 'gb-wls', 'NIR': 'gb-nir',
}


def flag_path(code):
    return os.path.join(gcfg.FLAG_DIR, f"{code.upper()}.png")


def fetch(code, quiet=False):
    """Downloads one flag if it is not there yet; True when the file exists afterwards."""
    code = (code or '').strip().upper()
    path = flag_path(code) if code else ''
    if not code or os.path.exists(path):
        return bool(code)
    src = CODES.get(code) or (code.lower() if len(code) == 2 else None)
    if not src:
        if not quiet:
            print(f"  No flag known for country code {code}")
        return False
    try:
        req = urllib.request.Request(SOURCE.format(src), headers={'User-Agent': 'BAD-AI graphics'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
        if not data.startswith(b'\x89PNG'):
            raise ValueError('not a PNG')
        os.makedirs(gcfg.FLAG_DIR, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return True
    except Exception as e:
        if not quiet:
            print(f"  Could not download the {code} flag: {e}")
        return False


def ensure(codes):
    """Fetches the flags these drivers need (once; afterwards they are read from Graphics/Flags)."""
    for code in {c.strip().upper() for c in codes if c and c.strip()}:
        fetch(code)


if __name__ == '__main__':
    wanted = sys.argv[1:] or sorted(CODES)
    ok = sum(fetch(c) for c in wanted)
    print(f"{ok}/{len(wanted)} flags in {gcfg.FLAG_DIR}")
