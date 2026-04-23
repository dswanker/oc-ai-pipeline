"""
pricing_engine.py — OpenClinica Pricing Calculation Engine

Rates are read from pricing_model.ini (Option B — baked-in rates).
When rates change, update rates_effective_date and values in the ini,
then re-upload the skill via the Skills API.
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


def get_list_price(module_key, segment, cfg):
    """Get monthly list price for a module/segment combo from ini."""
    lookup_key = f"{module_key}.{segment}"
    try:
        return float(cfg['subscription_list_prices'].get(lookup_key, 0))
    except (KeyError, ValueError):
        return 0.0


def get_volume_discount(n_studies, contract_years, cfg):
    """Look up volume/term discount from the table. Returns discount as decimal."""
    studies = min(n_studies, 10)
    years   = min(contract_years, 5)
    key = f"studies_{studies}.years_{years}"
    try:
        return float(cfg['volume_discounts'].get(key, 0))
    except (KeyError, ValueError):
        return 0.0


def extract_study_duration(pricing_summary, cfg):
    dur_cfg  = cfg['study_duration']
    fallback = int(dur_cfg.get('fallback_duration_months', 12))
    fields   = [f.strip() for f in
                dur_cfg.get('duration_field_names', 'total_study_duration_months').split(',')]

    search = [pricing_summary, pricing_summary.get('study_meta', {})]
    for d in search:
        for f in fields:
            if f in d and d[f]:
                try:
                    m = int(float(str(d[f])))
                    if m > 0:
                        return m, f"extracted from protocol summary ({f})"
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

    # Accept both bare category names (e.g. "site_specific") and the
    # _count suffix form that the protocol-analysis skill emits (e.g.
    # "site_specific_count").
    flags = {}
    for k, v in raw.items():
        norm_k = k[:-6] if k.endswith('_count') else k
        if isinstance(v, list):
            flags[norm_k] = len(v)
        elif isinstance(v, int):
            flags[norm_k] = v

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
    """Build the full module list with pricing applied."""
    ps_str = str(pricing_summary).lower()

    # Step 1: Determine which modules are included
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
                'name':        mod.get('name', key),
                'note':        mod.get('note', ''),
                'always':      always,
                'detected_by': 'always_included' if always else 'protocol_detected',
            }

    # Step 2: Check for Core Bundle eligibility
    bundle_cfg  = cfg['bundle']
    bundle_mods = [m.strip() for m in bundle_cfg.get('modules', '').split(',')]
    use_bundle  = all(m in included_keys for m in bundle_mods)

    # Step 3: Calculate volume/term discount
    vol_discount = get_volume_discount(n_studies, contract_years, cfg)

    # Step 4: Check platform discount eligibility
    plat_cfg       = cfg['platform_discount']
    plat_rate      = float(plat_cfg.get('rate', 0.20))
    plat_segments  = [s.strip().lower() for s in
                      plat_cfg.get('segments', 'commercial').split(',')]
    plat_min_mods  = int(plat_cfg.get('min_modules', 3))
    plat_required  = plat_cfg.get('required_core', 'core_edc')
    plat_required2 = plat_cfg.get('required_add', 'insight')
    bundle_blocks_plat = plat_cfg.get('platform_discount_with_bundle',
                                      'false').lower() == 'true'

    use_platform_discount = (
        segment in plat_segments and
        plat_required  in included_keys and
        plat_required2 in included_keys and
        len(included_keys) >= plat_min_mods and
        not (use_bundle and not bundle_blocks_plat)
    )

    # Step 5: Price each module
    modules = []

    if use_bundle:
        bundle_list_price = get_list_price('core_bundle', segment, cfg)
        discounted_price  = bundle_list_price * (1 - vol_discount)
        if use_platform_discount:
            discounted_price *= (1 - plat_rate)
        monthly = round(discounted_price, 2)
        total   = round(monthly * duration_months, 2)

        modules.append({
            'key':             'core_bundle',
            'name':            bundle_cfg.get('name', 'Core Bundle'),
            'list_price':      bundle_list_price,
            'vol_discount':    vol_discount,
            'plat_discount':   plat_rate if use_platform_discount else 0.0,
            'monthly_fee':     monthly,
            'duration_months': duration_months,
            'total_fee':       total,
            'always':          True,
            'detected_by':     'always_included',
            'note':            'Core EDC + Insight — bundle price applied',
            'is_bundle':       True,
        })
        remaining = [k for k in included_keys if k not in bundle_mods]
    else:
        remaining = included_keys

    for key in remaining:
        meta       = module_meta.get(key, {'name': key, 'note': '', 'always': False})
        list_price = get_list_price(key, segment, cfg)
        discounted = list_price * (1 - vol_discount)
        if use_platform_discount:
            discounted *= (1 - plat_rate)
        monthly = round(discounted, 2)
        total   = round(monthly * duration_months, 2)

        modules.append({
            'key':             key,
            'name':            meta['name'],
            'list_price':      list_price,
            'vol_discount':    vol_discount,
            'plat_discount':   plat_rate if use_platform_discount else 0.0,
            'monthly_fee':     monthly,
            'duration_months': duration_months,
            'total_fee':       total,
            'always':          meta.get('always', False),
            'detected_by':     meta.get('detected_by', ''),
            'note':            meta.get('note', ''),
            'is_bundle':       False,
        })

    rates_effective_date = cfg['rates'].get('rates_effective_date', 'unknown')

    pricing_context = {
        'segment':                   segment,
        'n_studies':                 n_studies,
        'contract_years':            contract_years,
        'volume_discount':           vol_discount,
        'volume_discount_display':   f"{int(vol_discount * 100)}%",
        'use_bundle':                use_bundle,
        'use_platform_discount':     use_platform_discount,
        'platform_discount':         plat_rate if use_platform_discount else 0.0,
        'platform_discount_display': f"{int(plat_rate * 100)}%" if use_platform_discount else "0%",
        'rates_effective_date':      rates_effective_date,
    }

    return modules, pricing_context


def merge_edc_flags(pricing_summary, edc_structure):
    """
    Merge Study Specification review_flags into the protocol summary flags.
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
            if ' — ' in entry_str:
                parts = entry_str.split(' — ', 1)
                enriched_items.append({'item': parts[0].strip(), 'comment': parts[1].strip()})
            elif ' - ' in entry_str:
                parts = entry_str.split(' - ', 1)
                enriched_items.append({'item': parts[0].strip(), 'comment': parts[1].strip()})
            else:
                enriched_items.append({'item': entry_str, 'comment': ''})
        enriched_flags[cat] = enriched_items

    for cat, items in ps_flags.items():
        if cat not in enriched_flags and isinstance(items, list) and items:
            enriched_flags[cat] = [{'item': str(i).strip(), 'comment': ''} for i in items]

    enriched['review_flags'] = enriched_flags
    return enriched


def calculate_quote(protocol_summary, config_path=None, edc_structure=None):
    """
    Main entry point.
    protocol_summary: the JSON output from the protocol-analysis skill (Protocol Summary JSON).
    edc_structure: optionally supply the Study Specification JSON to enrich flag comments.
    """
    if edc_structure:
        protocol_summary = merge_edc_flags(protocol_summary, edc_structure)

    cfg  = load_config(config_path)
    meta = protocol_summary.get('study_meta', {})
    if not meta:
        meta = {k: v for k, v in protocol_summary.items()
                if not isinstance(v, (dict, list))}

    segment    = (meta.get('customer_segment') or 'COMMERCIAL').upper()
    seg_key    = {'COMMERCIAL': 'commercial',
                  'ACADEMIC':   'academic',
                  'LOW_MARKET': 'low_market'}.get(segment, 'commercial')

    n_studies  = int(meta.get('volume_studies', 1) or 1)
    dur_months, dur_source = extract_study_duration(protocol_summary, cfg)
    contract_years = max(1, math.ceil(dur_months / 12))

    flag_data  = count_flagged_items(protocol_summary, cfg)
    build_fee  = calculate_build_fee(flag_data, cfg)
    modules, pricing_ctx = calculate_modules(
        protocol_summary, cfg, seg_key, dur_months, n_studies, contract_years
    )

    module_total = sum(m['total_fee'] for m in modules)
    grand_total  = build_fee['total_fee'] + module_total
    out_cfg      = cfg['output']

    return {
        'generated_date':  datetime.date.today().isoformat(),
        'study_meta':      meta,
        'study_duration':  {
            'months':         dur_months,
            'contract_years': contract_years,
            'source':         dur_source,
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
            'rates_effective_date': cfg['rates'].get('rates_effective_date', 'unknown'),
        },
        'currency_symbol': out_cfg.get('currency_symbol', '$'),
        'currency_code':   out_cfg.get('currency_code', 'USD'),
        'client_contingency_footnote': out_cfg.get(
            'client_contingency_footnote', 'Includes 20% scope contingency allowance'),
        '_raw_flags': (protocol_summary.get('review_flags') or
                       protocol_summary.get('flag_summary') or {}),
        '_has_edc_comments': edc_structure is not None,
    }


def fmt_currency(v, sym='$'): return f"{sym}{v:,.2f}"
def fmt_hours(h): return f"{h} hr{'s' if h != 1 else ''}"
