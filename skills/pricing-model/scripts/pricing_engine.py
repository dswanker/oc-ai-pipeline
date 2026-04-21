"""
pricing_engine.py — OpenClinica Pricing Calculation Engine

Fetches live subscription rates from Google Drive on each run.
Falls back to rates in pricing_model.ini if Drive is unreachable.
"""

import configparser, math, datetime, os, re

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'references', 'pricing_model.ini'
)

def load_config(config_path=None):
    cfg = configparser.ConfigParser()
    cfg.read(config_path or DEFAULT_CONFIG_PATH)
    return cfg

def _round_hours(hours, method='nearest'):
    if method == 'up':    return math.ceil(hours)
    if method == 'down':  return math.floor(hours)
    return round(hours)


# ── Live rate fetch from Google Drive ─────────────────────────────────────────
def fetch_live_rates(cfg):
    """
    Fetch the current pricing spreadsheet from Google Drive and parse
    the list prices. Returns a dict of {module.segment: monthly_price}
    or None if the fetch fails.

    This function is called at skill runtime via the Google Drive MCP tool.
    When running inside Claude with Drive connected, the skill should:
      1. Call Google Drive:read_file_content with the file_id from config
      2. Pass the returned text to parse_live_rates()
      3. Use the result to override the ini fallback prices

    The skill SKILL.md documents this flow.
    Returns None here — actual fetching happens at the Claude skill level.
    """
    return None


def parse_live_rates(drive_text):
    """
    Parse subscription list prices from Google Drive spreadsheet text.
    Returns dict {module_key.segment: monthly_price} or empty dict on failure.
    """
    rates = {}
    if not drive_text:
        return rates

    # Map from spreadsheet display names to our internal keys
    name_map = {
        'edc':            'core_edc',
        'core edc':       'core_edc',
        'ecoa':           'ecoa',
        'ecoa (participate)': 'ecoa',
        'participate':    'ecoa',
        'econsent':       'econsent',
        'insight':        'insight',
        'randomization':  'randomization',
        'randomize':      'randomization',
        'core bundle':    'core_bundle',
    }

    # Find the main price table rows: EDC | $2,600 | $1,100 | $1,300
    segments = ['commercial', 'academic', 'low_market']

    for line in drive_text.split('\n'):
        # Strip markdown table formatting
        cells = [c.strip().strip('\\').strip() for c in line.split('|') if c.strip()]
        if len(cells) < 4:
            continue

        # First cell should be a module name
        label = cells[0].lower().strip('* ')
        key   = name_map.get(label)
        if not key:
            continue

        # Parse price cells — expect $X,XXX pattern
        prices = []
        for cell in cells[1:4]:
            m = re.search(r'\$([\d,]+)', cell)
            if m:
                prices.append(float(m.group(1).replace(',', '')))

        if len(prices) == 3:
            for seg, price in zip(segments, prices):
                rates[f"{key}.{seg}"] = price

    return rates


def get_list_price(module_key, segment, cfg):
    """Get monthly list price for a module/segment combo.

    Reads from cfg['subscription_list_prices'].  Live-rate overrides are
    applied in-memory to cfg by calculate_quote() before this is called, so
    no live_rates parameter is needed here.
    """
    lookup_key = f"{module_key}.{segment}"
    try:
        return float(cfg['subscription_list_prices'].get(lookup_key, 0))
    except (KeyError, ValueError):
        return 0.0


def get_volume_discount(n_studies, contract_years, cfg):
    """Look up volume/term discount from the table. Returns discount as decimal."""
    studies = min(n_studies, 10)   # cap at 10; beyond = custom
    years   = min(contract_years, 5)  # cap at 5

    key = f"studies_{studies}.years_{years}"
    try:
        return float(cfg['volume_discounts'].get(key, 0))
    except (KeyError, ValueError):
        return 0.0


def extract_study_duration(pricing_summary, cfg):
    dur_cfg    = cfg['study_duration']
    fallback   = int(dur_cfg.get('fallback_duration_months', 12))
    fields     = [f.strip() for f in
                  dur_cfg.get('duration_field_names', 'total_study_duration_months').split(',')]

    search = [pricing_summary, pricing_summary.get('study_meta', {})]
    for d in search:
        for f in fields:
            if f in d and d[f]:
                try:
                    m = int(float(str(d[f])))
                    if m > 0:
                        return m, f"extracted from pricing summary ({f})"
                except (ValueError, TypeError):
                    pass
    return fallback, f"fallback default ({fallback} months)"


def count_flagged_items(pricing_summary, cfg):
    flag_cfg = cfg['flag_categories']
    counted  = [c for c, v in flag_cfg.items() if v.strip().lower() == 'true']
    excluded = [c for c, v in flag_cfg.items() if v.strip().lower() == 'false']

    raw = (pricing_summary.get('review_flags') or
           pricing_summary.get('flag_summary') or
           pricing_summary)

    # Normalise — handle both plain strings and {item, comment} dicts
    flags = {}
    for k, v in raw.items():
        if isinstance(v, list):
            flags[k] = len(v)
        elif isinstance(v, int):
            flags[k] = v

    category_counts = {}
    total_counted = total_excluded = 0

    for cat in counted:
        count = next((flags[k] for k in flags if k.lower() == cat.lower()), 0)
        category_counts[cat] = count
        total_counted += count

    for cat in excluded:
        count = next((flags[k] for k in flags if k.lower() == cat.lower()), 0)
        total_excluded += count

    return {
        'category_counts':        category_counts,
        'counted_categories':     counted,
        'excluded_categories':    excluded,
        'total_flagged_counted':  total_counted,
        'total_flagged_excluded': total_excluded,
        'total_flagged_all':      total_counted + total_excluded,
    }


def calculate_build_fee(flag_data, cfg):
    rates    = cfg['rates']
    rounding = cfg['rounding']

    ps_rate    = float(rates['ps_hourly_rate'])
    mins_item  = float(rates['minutes_per_flagged_item'])
    cont_pct   = float(rates['contingency_pct'])
    pm_hrs     = float(rates.get('project_management_hours', 40))
    base_round = rounding.get('base_hours_rounding', 'nearest')
    cont_round = rounding.get('contingency_hours_rounding', 'nearest')

    total_items    = flag_data['total_flagged_counted']
    raw_hours      = total_items * (mins_item / 60.0)
    base_hours     = _round_hours(raw_hours, base_round)
    pre_cont_hours = base_hours + int(pm_hrs)
    cont_raw       = pre_cont_hours * cont_pct
    cont_hours     = _round_hours(cont_raw, cont_round)
    total_hours    = pre_cont_hours + cont_hours

    return {
        'flagged_items':           total_items,
        'minutes_per_item':        mins_item,
        'raw_hours':               raw_hours,
        'base_hours':              base_hours,
        'pm_hours':                int(pm_hrs),
        'pre_cont_hours':          pre_cont_hours,
        'contingency_pct':         cont_pct,
        'contingency_pct_display': f"{int(cont_pct * 100)}%",
        'contingency_hours':       cont_hours,
        'total_hours':             total_hours,
        'hourly_rate':             ps_rate,
        'base_fee':                base_hours     * ps_rate,
        'pm_fee':                  int(pm_hrs)    * ps_rate,
        'contingency_fee':         cont_hours     * ps_rate,
        'total_fee':               total_hours    * ps_rate,
    }


def calculate_modules(pricing_summary, cfg, segment, duration_months,
                      n_studies, contract_years):
    """
    Build the full module list with pricing applied.
    Returns list of module dicts and a pricing_context dict.
    """
    ps_str = str(pricing_summary).lower()

    # ── Step 1: Determine which modules are included ───────────────────────────
    included_keys = []
    module_meta   = {}

    for section in cfg.sections():
        if not section.startswith('modules.'):
            continue
        mod        = cfg[section]
        key        = mod.get('key', section.replace('modules.', ''))
        always     = mod.get('always_include', 'false').strip().lower() == 'true'
        trigger_str= mod.get('trigger', '').strip().lower()

        detected = False
        if always:
            detected = True
        elif trigger_str:
            triggers = [t.strip() for t in trigger_str.split(',') if t.strip()]
            detected = any(t in ps_str for t in triggers)

        if detected:
            included_keys.append(key)
            module_meta[key] = {
                'name':         mod.get('name', key),
                'note':         mod.get('note', ''),
                'always':       always,
                'detected_by':  'always_included' if always else 'protocol_detected',
            }

    # ── Step 2: Check for Core Bundle eligibility ──────────────────────────────
    bundle_cfg   = cfg['bundle']
    bundle_mods  = [m.strip() for m in bundle_cfg.get('modules', '').split(',')]
    use_bundle   = all(m in included_keys for m in bundle_mods)

    # ── Step 3: Calculate volume/term discount ────────────────────────────────
    vol_discount = get_volume_discount(n_studies, contract_years, cfg)

    # ── Step 4: Check platform discount eligibility ────────────────────────────
    plat_cfg         = cfg['platform_discount']
    plat_rate        = float(plat_cfg.get('rate', 0.20))
    plat_segments    = [s.strip().lower() for s in
                        plat_cfg.get('segments', 'commercial').split(',')]
    plat_min_mods    = int(plat_cfg.get('min_modules', 3))
    plat_required    = plat_cfg.get('required_core', 'core_edc')
    plat_required2   = plat_cfg.get('required_add', 'insight')
    bundle_blocks_plat = plat_cfg.get('platform_discount_with_bundle', 'false').lower() == 'true'

    use_platform_discount = (
        segment in plat_segments and
        plat_required  in included_keys and
        plat_required2 in included_keys and
        len(included_keys) >= plat_min_mods and
        not (use_bundle and not bundle_blocks_plat)
    )

    # ── Step 5: Price each module ──────────────────────────────────────────────
    modules = []

    if use_bundle:
        # Replace core_edc + insight with bundle pricing
        bundle_list_price = get_list_price('core_bundle', segment, cfg)
        discounted_price  = bundle_list_price * (1 - vol_discount)
        if use_platform_discount:
            discounted_price *= (1 - plat_rate)
        monthly = round(discounted_price, 2)
        total   = round(monthly * duration_months, 2)

        modules.append({
            'key':              'core_bundle',
            'name':             bundle_cfg.get('name', 'Core Bundle'),
            'list_price':       bundle_list_price,
            'vol_discount':     vol_discount,
            'plat_discount':    plat_rate if use_platform_discount else 0.0,
            'monthly_fee':      monthly,
            'duration_months':  duration_months,
            'total_fee':        total,
            'always':           True,
            'detected_by':      'always_included',
            'note':             'Core EDC + Insight — bundle price applied',
            'is_bundle':        True,
        })

        # Price remaining modules (not in bundle)
        remaining = [k for k in included_keys if k not in bundle_mods]
    else:
        remaining = included_keys

    for key in remaining:
        meta        = module_meta.get(key, {'name': key, 'note': '', 'always': False})
        list_price  = get_list_price(key, segment, cfg)
        discounted  = list_price * (1 - vol_discount)
        if use_platform_discount:
            discounted *= (1 - plat_rate)
        monthly = round(discounted, 2)
        total   = round(monthly * duration_months, 2)

        modules.append({
            'key':              key,
            'name':             meta['name'],
            'list_price':       list_price,
            'vol_discount':     vol_discount,
            'plat_discount':    plat_rate if use_platform_discount else 0.0,
            'monthly_fee':      monthly,
            'duration_months':  duration_months,
            'total_fee':        total,
            'always':           meta.get('always', False),
            'detected_by':      meta.get('detected_by', ''),
            'note':             meta.get('note', ''),
            'is_bundle':        False,
        })

    pricing_context = {
        'segment':                 segment,
        'n_studies':               n_studies,
        'contract_years':          contract_years,
        'volume_discount':         vol_discount,
        'volume_discount_display': f"{int(vol_discount * 100)}%",
        'use_bundle':              use_bundle,
        'use_platform_discount':   use_platform_discount,
        'platform_discount':       plat_rate if use_platform_discount else 0.0,
        'platform_discount_display': f"{int(plat_rate * 100)}%" if use_platform_discount else "0%",
        # rates_effective_date is injected by calculate_quote() after this returns
    }

    return modules, pricing_context


def merge_edc_flags(pricing_summary, edc_structure):
    """
    Merge EDC structure review_flags into the pricing summary flags.
    EDC structure flags have full sentence descriptions — these replace
    the shorter pricing summary strings when supplied (Option A).

    Each EDC flag entry like:
      "Lab ranges CSV — all lower/upper/unit values require site-specific input"
    is split into:
      item    = "Lab ranges CSV"
      comment = "all lower/upper/unit values require site-specific input"

    Returns an enriched review_flags dict with {item, comment} pairs.
    """
    if not edc_structure:
        return pricing_summary

    edc_flags = edc_structure.get('review_flags', {})
    if not edc_flags:
        return pricing_summary

    enriched = dict(pricing_summary)
    enriched_flags = {}

    ps_flags = (pricing_summary.get('review_flags') or
                pricing_summary.get('flag_summary') or {})

    for cat, items in edc_flags.items():
        if not isinstance(items, list) or not items:
            continue
        enriched_items = []
        for entry in items:
            entry_str = str(entry).strip()
            # Split on first ' — ' (em-dash style) or ' - ' to get item + comment
            if ' — ' in entry_str:
                parts = entry_str.split(' — ', 1)
                enriched_items.append({
                    'item':    parts[0].strip(),
                    'comment': parts[1].strip()
                })
            elif ' - ' in entry_str:
                parts = entry_str.split(' - ', 1)
                enriched_items.append({
                    'item':    parts[0].strip(),
                    'comment': parts[1].strip()
                })
            else:
                # No separator — full string is the item, no comment
                enriched_items.append({
                    'item':    entry_str,
                    'comment': ''
                })
        enriched_flags[cat] = enriched_items

    # For categories in pricing summary but not in EDC structure,
    # keep the pricing summary entries as plain strings
    for cat, items in ps_flags.items():
        if cat not in enriched_flags and isinstance(items, list) and items:
            enriched_flags[cat] = [
                {'item': str(i).strip(), 'comment': ''} for i in items
            ]

    enriched['review_flags'] = enriched_flags
    return enriched


def calculate_quote(pricing_summary, config_path=None, live_rates=None,
                    edc_structure=None):
    """
    Main entry point. live_rates can be passed in when the skill has
    fetched fresh data from Google Drive.
    edc_structure can optionally be supplied to enrich flag comments.
    """
    # Merge EDC structure flags if provided (Option A enrichment)
    if edc_structure:
        pricing_summary = merge_edc_flags(pricing_summary, edc_structure)
    cfg  = load_config(config_path)

    # ── Apply live rates (if any) as in-memory cfg overrides ──────────────────
    # The caller (skill layer) fetches live rates from Google Drive via MCP and
    # passes them here.  We patch cfg['subscription_list_prices'] in-memory so
    # that get_list_price() / calculate_modules() need no live_rates parameter.
    if live_rates:
        for key, price in live_rates.items():
            cfg['subscription_list_prices'][key] = str(price)
        rates_effective_date = datetime.date.today().isoformat()
    else:
        rates_effective_date = "config baseline"
    meta = pricing_summary.get('study_meta', {})
    if not meta:
        meta = {k: v for k, v in pricing_summary.items()
                if not isinstance(v, (dict, list))}

    # Commercial context from pricing summary
    segment       = (meta.get('customer_segment') or 'COMMERCIAL').upper()
    seg_key       = {'COMMERCIAL': 'commercial',
                     'ACADEMIC':   'academic',
                     'LOW_MARKET': 'low_market'}.get(segment, 'commercial')

    n_studies     = int(meta.get('volume_studies', 1) or 1)
    dur_months, dur_source = extract_study_duration(pricing_summary, cfg)
    contract_years = max(1, math.ceil(dur_months / 12))

    flag_data  = count_flagged_items(pricing_summary, cfg)
    build_fee  = calculate_build_fee(flag_data, cfg)
    modules, pricing_ctx = calculate_modules(
        pricing_summary, cfg, seg_key, dur_months,
        n_studies, contract_years
    )
    pricing_ctx['rates_effective_date'] = rates_effective_date

    module_total = sum(m['total_fee'] for m in modules)
    grand_total  = build_fee['total_fee'] + module_total
    out_cfg      = cfg['output']

    return {
        'generated_date': datetime.date.today().isoformat(),
        'study_meta':     meta,
        'study_duration': {
            'months':          dur_months,
            'contract_years':  contract_years,
            'source':          dur_source,
        },
        'pricing_context': pricing_ctx,
        'flag_analysis':   flag_data,
        'build_fee':       build_fee,
        'modules':         modules,
        'totals': {
            'build_fee':    build_fee['total_fee'],
            'module_total': module_total,
            'grand_total':  grand_total,
        },
        'config_snapshot': {
            'ps_hourly_rate':      float(cfg['rates']['ps_hourly_rate']),
            'minutes_per_item':    float(cfg['rates']['minutes_per_flagged_item']),
            'contingency_pct':     float(cfg['rates']['contingency_pct']),
            'pm_hours':            float(cfg['rates'].get('project_management_hours', 40)),
            'excluded_categories': flag_data['excluded_categories'],
            'drive_file_id':       cfg['google_drive'].get('file_id', ''),
        },
        'currency_symbol': out_cfg.get('currency_symbol', '$'),
        'currency_code':   out_cfg.get('currency_code', 'USD'),
        'client_contingency_footnote': out_cfg.get(
            'client_contingency_footnote', 'Includes 20% scope contingency allowance'),
        # Raw flag data for appendix item-level display
        # May contain plain strings or {item, comment} dicts (if EDC structure supplied)
        '_raw_flags': (pricing_summary.get('review_flags') or
                       pricing_summary.get('flag_summary') or {}),
        '_has_edc_comments': edc_structure is not None,
    }


def fmt_currency(v, sym='$'): return f"{sym}{v:,.2f}"
def fmt_hours(h): return f"{h} hr{'s' if h != 1 else ''}"


if __name__ == '__main__':
    sample = {
        'study_meta': {
            'protocol_number':             'PrTK05',
            'study_title':                 'CAN-2409 Phase 2a Prostate Cancer Study',
            'sponsor':                     'Candel Therapeutics',
            'study_phase':                 'Phase 2a',
            'indication':                  'Prostate Cancer',
            'customer_segment':            'COMMERCIAL',
            'volume_studies':              1,
            'total_study_duration_months': 24,
        },
        'review_flags': {
            'site_specific':         ['Lab ranges', 'LBNAM', 'Site count'],
            'oid_confirmation':      [],
            'protocol_ambiguous':    ['BE qPCR', 'Biomarker list', 'BES type', 'SE_UNSCH', 'DC'],
            'constraint_review':     ['VS window', 'LB window', 'EBRT date', 'EC dates', 'EXDOSE'],
            'choice_list_review':    ['IE003CD', 'DSDECOD'],
            'custom_domain':         ['BE Lab Manual', 'EC_DIARY', 'DC sponsor'],
            'pdf_mapping_uncertain': [],
            'name_deviation':        [],
        },
        'is_epro_required': True,
    }

    q  = calculate_quote(sample)
    bf = q['build_fee']
    fa = q['flag_analysis']
    pc = q['pricing_context']
    s  = q['currency_symbol']
    d  = q['study_duration']

    print("=" * 62)
    print(f"OPENCLINICA QUOTE — {q['study_meta']['protocol_number']}")
    print("=" * 62)
    print(f"Segment:        {pc['segment'].title()}  |  "
          f"Studies: {pc['n_studies']}  |  "
          f"Contract: {d['contract_years']} yr(s)  |  "
          f"Duration: {d['months']} mo")
    print(f"Volume disc:    {pc['volume_discount_display']}  |  "
          f"Platform disc: {pc['platform_discount_display']}  |  "
          f"Bundle: {'Yes' if pc['use_bundle'] else 'No'}  |  "
          f"Rates eff: {pc['rates_effective_date']}")
    print()
    print("ONE-TIME FEES")
    print(f"  {fa['total_flagged_counted']} items × {bf['minutes_per_item']:.0f} min "
          f"→ {bf['base_hours']} base hrs + {bf['pm_hours']} PM = "
          f"{bf['pre_cont_hours']} pre-cont + {bf['contingency_hours']} cont "
          f"= {bf['total_hours']} total hrs")
    print(f"  TOTAL BUILD FEE:   {fmt_currency(bf['total_fee'], s)}")
    print()
    print("SUBSCRIPTIONS (monthly × duration)")
    for m in q['modules']:
        disc = (1 - (1-m['vol_discount']) * (1-m['plat_discount'])) * 100
        print(f"  {m['name']:<32} list {fmt_currency(m['list_price'],s)}/mo  "
              f"disc {disc:.0f}%%  net {fmt_currency(m['monthly_fee'],s)}/mo  "
              f"total {fmt_currency(m['total_fee'],s)}")
    print(f"  MODULE TOTAL:      {fmt_currency(q['totals']['module_total'], s)}")
    print()
    print(f"  GRAND TOTAL:       {fmt_currency(q['totals']['grand_total'], s)}")
    print("=" * 62)
