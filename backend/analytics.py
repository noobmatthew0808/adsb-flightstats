"""Coverage-aware analytics. Raw Aerodrome observations are never copied or changed.

Sessions are estimates: a new session starts after > gap minutes, or an immediate
change between two non-empty callsigns. Missing callsigns alone do not split.
Sessions belong to their start timestamp; a carry-in session is not a new flight.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import re
import threading
import time
from zoneinfo import ZoneInfo

from .db import Database, DatabaseError, TABLES

UTC = timezone.utc
LONDON = ZoneInfo('Europe/London')
GROUPS = {'minute', 'hour', 'day', 'week', 'month', 'year', 'weekday', 'hour_of_day', 'month_of_year'}
METRICS = {'aircraft', 'sessions', 'observations'}
STATS = {'count', 'average', 'cumulative', 'moving_average'}
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']


def integer(value, default, lo, hi):
    try:
        number = int(value if value not in (None, '') else default)
    except (ValueError, TypeError):
        raise ValueError('Expected a whole number.') from None
    if not lo <= number <= hi:
        raise ValueError(f'Number must be between {lo} and {hi}.')
    return number


def floor_timestamp(timestamp: int, group: str) -> int:
    local = datetime.fromtimestamp(timestamp, LONDON)
    if group in ('hour', 'minute'):
        # Preserve fold: the two 01:00 hours on the autumn clock change differ.
        return int(local.replace(second=0, microsecond=0, minute=0 if group == 'hour' else local.minute).timestamp())
    local = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
    if group == 'week':
        local -= timedelta(days=local.weekday())
    elif group == 'month':
        local = local.replace(day=1)
    elif group == 'year':
        local = local.replace(month=1, day=1)
    return int(local.timestamp())


def next_timestamp(timestamp: int, group: str) -> int:
    if group == 'minute':
        return timestamp + 60
    if group == 'hour':
        return timestamp + 3600
    local = datetime.fromtimestamp(timestamp, LONDON)
    if group == 'day':
        local += timedelta(days=1)
    elif group == 'week':
        local += timedelta(days=7)
    elif group == 'month':
        local = local.replace(year=local.year + (local.month == 12), month=local.month % 12 + 1)
    elif group == 'year':
        local = local.replace(year=local.year + 1)
    return int(local.timestamp())


def selected_seconds(start: int, end: int, month: int | None) -> int:
    """Elapsed seconds in selected London calendar months, including DST."""
    if end <= start:
        return 0
    if month is None:
        return end - start
    total = 0
    cursor = start
    while cursor < end:
        local = datetime.fromtimestamp(cursor, LONDON)
        boundary = next_timestamp(floor_timestamp(cursor, 'month'), 'month')
        stop = min(end, boundary)
        if local.month == month:
            total += stop - cursor
        cursor = stop
    return total


def bucket_label(timestamp: int, group: str) -> str:
    dt = datetime.fromtimestamp(timestamp, LONDON)
    return dt.strftime({'minute':'%d %b %H:%M %Z', 'hour':'%d %b %H:%M %Z', 'day':'%d %b %Y', 'week':'Week of %d %b %Y', 'month':'%b %Y', 'year':'%Y'}[group])


def params_normalized(params: dict) -> dict:
    now = int(time.time())
    p = dict(params)
    p['end'] = integer(p.get('end'), now, 0, 7258118400)
    p['start'] = integer(p.get('start'), p['end'] - 86400, 0, 7258118400)
    if p['end'] <= p['start'] or p['end'] - p['start'] > 366 * 86400 * 30:
        raise ValueError('Select a positive period of at most 30 years.')
    for name, allowed, default in [('metric', METRICS, 'aircraft'), ('group', GROUPS, 'hour'), ('stat', STATS, 'count')]:
        p[name] = p.get(name) or default
        if p[name] not in allowed:
            raise ValueError(f'Unsupported {name}.')
    p['session_gap'] = integer(p.get('session_gap'), 30, 1, 240)
    p['month'] = integer(p['month'], 1, 1, 12) if p.get('month') not in (None, '') else None
    p['exclude_partial'] = str(p.get('exclude_partial', '')).lower() in ('1', 'true', 'yes')
    for name in ('category', 'aircraft_type', 'operator', 'icao', 'callsign'):
        p[name] = str(p.get(name) or '').strip()
        if len(p[name]) > 100:
            raise ValueError('Filter text is too long.')
    return p


class Analytics:
    def __init__(self, db: Database):
        self.db = db
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.RLock()

    def status(self) -> dict:
        result = {'connected': False, 'path': str(self.db.path), 'error': None,
                  'first_seen': None, 'last_seen': None, 'observations': 0, 'aircraft': 0, 'bytes': 0}
        try:
            with self.db.connect() as con:
                row = con.execute('SELECT MIN(seen_at), MAX(seen_at), COUNT(*), COUNT(DISTINCT UPPER(icao)) FROM all_sightings').fetchone()
                result.update(connected=True, first_seen=row[0], last_seen=row[1], observations=row[2], aircraft=row[3], bytes=self.db.path.stat().st_size)
        except (DatabaseError, OSError) as error:
            result['error'] = str(error)
        return result

    def _source(self, con, p):
        available = self.db.available(con)
        if 'all_sightings' not in available:
            raise DatabaseError('Source does not contain all_sightings. This version requires the verified Aerodrome raw-history schema.')
        meta_cols = {r['name'] for r in self.db.columns(con, 'seen_aircraft')} if 'seen_aircraft' in available else set()
        metadata_fields = ['aircraft_type', 'operator', 'category', 'registration', 'country', 'aircraft_type_desc']
        if meta_cols:
            fields = ','.join(f'MAX(NULLIF(TRIM("{name}"),\'\')) AS "{name}"' if name in meta_cols else f'NULL AS "{name}"' for name in metadata_fields)
            meta = f'SELECT UPPER(icao) AS icao,{fields} FROM seen_aircraft GROUP BY UPPER(icao)'
        else:
            meta = "SELECT NULL AS icao," + ','.join(f'NULL AS "{name}"' for name in metadata_fields) + ' WHERE 0'
        where = ['r.seen_at >= :start', 'r.seen_at < :end']
        values = {'start':p['start'], 'end':p['end'], 'before':max(0,p['start'] - p['session_gap'] * 60), 'gap':p['session_gap'] * 60}
        for name, expr in [('category', "COALESCE(NULLIF(m.category,''),'unknown')"), ('aircraft_type', "COALESCE(NULLIF(r.aircraft_type,''),m.aircraft_type,'Unknown')"), ('operator', "COALESCE(NULLIF(m.operator,''),'Unknown')"), ('icao','r.icao')]:
            if p[name]:
                where.append(f'LOWER({expr}) = LOWER(:{name})')
                values[name] = p[name]
        if p['callsign']:
            where.append("UPPER(COALESCE(r.callsign,'')) LIKE :callsign ESCAPE '\\'")
            values['callsign'] = p['callsign'].upper().replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
        if p['month']:
            con.create_function('london_month', 1, lambda t: datetime.fromtimestamp(t, LONDON).month, deterministic=True)
            where.append('london_month(r.seen_at) = :month')
            values['month'] = p['month']
        source = f'''WITH metadata AS ({meta}),
            ordered AS (
              SELECT a.*, LAG(seen_at) OVER (PARTITION BY UPPER(icao) ORDER BY seen_at,id) AS prev_at,
                     LAG(NULLIF(TRIM(callsign),'')) OVER (PARTITION BY UPPER(icao) ORDER BY seen_at,id) AS prev_callsign
              FROM all_sightings a WHERE seen_at >= :before AND seen_at < :end
            ), events AS (
              SELECT r.*, COALESCE(NULLIF(r.aircraft_type,''),m.aircraft_type,'Unknown') AS type_name,
                  COALESCE(NULLIF(m.operator,''),'Unknown') AS operator_name,
                  COALESCE(NULLIF(m.category,''),'unknown') AS category_name,
                  CASE WHEN NULLIF(TRIM(r.icao),'') IS NULL THEN 0
                    WHEN prev_at IS NULL OR r.seen_at - prev_at > :gap
                    OR (NULLIF(TRIM(r.callsign),'') IS NOT NULL AND prev_callsign IS NOT NULL
                        AND UPPER(TRIM(r.callsign)) <> UPPER(prev_callsign)) THEN 1 ELSE 0 END AS session_start
              FROM ordered r LEFT JOIN metadata m ON UPPER(r.icao)=m.icao WHERE {' AND '.join(where)}
            )'''
        return source, values

    def _coverage(self, con, p):
        """Compress consecutive recorded minutes into read-only coverage spans.

        SQL aggregation avoids retaining millions of minute rows for multi-year
        calendar analyses. A missing minute always breaks a span.
        """
        available = self.db.available(con)
        method = 'concurrent_minute record presence, including recorded zero-aircraft minutes; collection completeness is inferred, not guaranteed'
        args = [p['start'] // 60 * 60, p['end']]
        month_clause = ''
        if 'concurrent_minute' in available:
            if p['month']:
                month_clause = ' AND london_month(minute_bucket)=?'
                args.append(p['month'])
            moments = 'SELECT minute_bucket AS minute, MAX(count) AS peak FROM concurrent_minute WHERE minute_bucket >= ? AND minute_bucket < ?' + month_clause + ' GROUP BY minute_bucket'
        else:
            method = 'sighting-minute lower bound; silence cannot distinguish no traffic from collection downtime'
            if p['month']:
                month_clause = ' AND london_month(seen_at)=?'
                args.append(p['month'])
            moments = 'SELECT CAST(seen_at/60 AS INTEGER)*60 AS minute, NULL AS peak FROM all_sightings WHERE seen_at >= ? AND seen_at < ?' + month_clause + ' GROUP BY 1'
        spans = con.execute('WITH moments AS (' + moments + "), numbered AS (SELECT minute,peak,minute-ROW_NUMBER() OVER(ORDER BY minute)*60 AS island FROM moments) SELECT MIN(minute),MAX(minute)+60,MAX(peak) FROM numbered GROUP BY island ORDER BY MIN(minute) LIMIT 100001", args).fetchall()
        if len(spans)>100000:
            raise ValueError('Too many collection gaps to analyze safely. Select a shorter range.')
        return [(int(r[0]),int(r[1]),r[2]) for r in spans], method

    def query(self, params: dict) -> dict:
        p = params_normalized(params)
        key = json.dumps(p, sort_keys=True)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                return json.loads(json.dumps(cached[1]))
        result = self._query(p)
        with self._lock:
            if len(self._cache) >= 32:
                self._cache.clear()
            self._cache[key] = (time.monotonic() + 20, result)
        return result

    def _query(self, p):
        group = p['group']
        base = {'weekday':'day','hour_of_day':'hour','month_of_year':'month'}.get(group, group)
        # Calendar averages are means of daily counts, not divided distinct totals.
        if p['stat'] == 'average' and group in ('week','month','year'):
            base = 'day'
        periods = []
        t = floor_timestamp(p['start'], base)
        while t < p['end']:
            period_limit = 300000 if group in ('weekday','hour_of_day','month_of_year') else 10000
            if len(periods) >= period_limit:
                raise ValueError('More than 10,000 chart buckets. Choose a coarser grouping or shorter range.')
            nxt = next_timestamp(t, base)
            if selected_seconds(max(t,p['start']), min(nxt,p['end']), p['month']) > 0:
                periods.append((t,nxt))
            t = nxt
        boundaries = {t:n for t,n in periods}
        if not periods:
            periods = []
        coverage_seconds = defaultdict(float)
        requested = defaultdict(float)
        for t,n in periods:
            requested[t] = selected_seconds(max(t,p['start']), min(n,p['end']), p['month'])
        warnings = []
        with self.db.connect() as con:
            source, values = self._source(con,p)
            minute_rows, method = self._coverage(con,p)
            for minute, span_end, _ in minute_rows:
                stop = min(span_end,p['end'])
                cursor = max(minute,p['start'])
                while cursor < stop:
                    bucket = floor_timestamp(cursor, base)
                    end = min(stop,next_timestamp(bucket,base))
                    coverage_seconds[bucket] += end-cursor
                    cursor = end
            bucket_cache = {}
            def bucket(timestamp):
                # Minute cache preserves DST fold and minimizes Python callbacks.
                key = timestamp // 60
                if key not in bucket_cache:
                    bucket_cache[key] = floor_timestamp(timestamp,base)
                return bucket_cache[key]
            con.create_function('bucket',1,bucket, deterministic=True)
            rows = con.execute(source + ''' SELECT bucket(seen_at) AS bucket, UPPER(icao) AS icao, type_name, operator_name, category_name,
                    COUNT(*) AS observations, SUM(session_start) AS sessions
                 FROM events GROUP BY 1,2,3,4,5 LIMIT 200001''',values).fetchall()
            if len(rows)>200000:
                raise ValueError('This analysis exceeds 200,000 aircraft/bucket groups. Narrow the period or filters.')
        # An observation can exist when the minute aggregate is absent. Its
        # bucket remains visible, but absence of coverage is still disclosed.
        bucket_metrics = defaultdict(lambda:{'aircraft':set(),'observations':0,'sessions':0})
        breakdown = {name:defaultdict(lambda:{'aircraft':set(),'observations':0,'sessions':0}) for name in ('types','operators','categories')}
        total = {'aircraft':set(),'observations':0,'sessions':0}
        heat = defaultdict(lambda: {'aircraft':set(),'observations':0,'sessions':0})
        operator_known = set()
        eligible = set()
        for t,n in periods:
            full_requested = selected_seconds(t,n,p['month'])
            whole = requested[t] == full_requested
            covered = coverage_seconds[t] >= full_requested-1
            if not p['exclude_partial'] or (whole and covered):
                eligible.add(t)
        for row in rows:
            timestamp = row['bucket']
            if timestamp not in eligible:
                continue
            icao = row['icao']
            for container in (total,bucket_metrics[timestamp]):
                if icao:
                    container['aircraft'].add(icao)
                container['observations'] += row['observations']
                container['sessions'] += row['sessions']
            if icao and row['operator_name'] != 'Unknown':
                operator_known.add(icao)
            for name,column in [('types','type_name'),('operators','operator_name'),('categories','category_name')]:
                item = breakdown[name][row[column]]
                if icao:
                    item['aircraft'].add(icao)
                item['observations'] += row['observations']
                item['sessions'] += row['sessions']
            hour_start = floor_timestamp(timestamp,'hour')
            h = heat[hour_start]
            if icao:
                h['aircraft'].add(icao)
            h['observations'] += row['observations']
            h['sessions'] += row['sessions']
        def count(container):
            value = container[p['metric']]
            return len(value) if isinstance(value,set) else value
        output_groups = defaultdict(list)
        for t,n in periods:
            if t not in eligible:
                continue
            dt = datetime.fromtimestamp(t,LONDON)
            if group == 'weekday':
                outkey = dt.weekday()
            elif group == 'hour_of_day':
                outkey = dt.hour
            elif group == 'month_of_year':
                outkey = dt.month
            else:
                outkey = floor_timestamp(t,group)
            output_groups[outkey].append(t)
        # Keep categorical axes stable even with sparse/missing history.
        if group in ('weekday','hour_of_day','month_of_year'):
            for k in (range(7) if group=='weekday' else range(24) if group=='hour_of_day' else range(1,13)):
                output_groups[k]
        series = []
        for k, members in sorted(output_groups.items()):
            active_members = [t for t in members if coverage_seconds[t] > 0 or bucket_metrics[t]['observations']]
            containers = [bucket_metrics[t] for t in active_members]
            data_count = 0
            if p['metric']=='aircraft':
                data_count = len(set().union(*(x['aircraft'] for x in containers))) if containers else 0
            else:
                data_count = sum(x[p['metric']] for x in containers)
            if p['stat']=='average':
                value = sum(count(x) for x in containers)/len(active_members) if active_members else None
            else:
                value = data_count if active_members else None
            req = sum(requested[t] for t in members)
            active = sum(min(coverage_seconds[t],requested[t]) for t in members)
            if group in ('weekday','hour_of_day','month_of_year'):
                label = WEEKDAYS[k] if group=='weekday' else f'{k:02d}:00' if group=='hour_of_day' else MONTHS[k-1]
                start = min(members) if members else None
                end = max((boundaries[t] for t in members),default=None)
            else:
                label = bucket_label(k,group)
                start,end = k,next_timestamp(k,group)
            series.append({'key':str(k),'label':label,'start':start,'end':end,'value':round(value,3) if value is not None else None,
                           'count':data_count if active_members else None,'coverage_pct':round(active/req*100,2) if req else 0,'samples':len(active_members)})
        if p['stat']=='cumulative':
            if p['metric']=='aircraft':
                seen=set()
                for item in series:
                    for t in output_groups[int(item['key'])]:
                        seen.update(bucket_metrics[t]['aircraft'])
                    item['value']=len(seen) if item['value'] is not None else None
            else:
                running=0
                for item in series:
                    if item['value'] is not None:
                        running+=item['value']
                        item['value']=running
        elif p['stat']=='moving_average':
            previous=[item['value'] for item in series]
            for i,item in enumerate(series):
                valid=[v for v in previous[max(0,i-6):i+1] if v is not None]
                item['value']=round(sum(valid)/len(valid),3) if valid and previous[i] is not None else None
        req_seconds=sum(requested.values())
        active_seconds=sum(min(coverage_seconds[t],requested[t]) for t in requested)
        coverage_pct=active_seconds/req_seconds*100 if req_seconds else 0
        if coverage_pct < 99.9:
            warnings.append('Incomplete collection evidence: missing time is not zero traffic. Comparisons and averages may be biased.')
        if not any(x['observations'] for x in bucket_metrics.values()):
            warnings.append('No matching observations in this period. Check available history and filters.')
        if p['exclude_partial']:
            warnings.append('Only whole buckets with complete recorded-minute coverage are included. Excluded periods are not zero.')
        elif p['stat']=='average':
            warnings.append('Average denominator is the number of periods with collection evidence. Partial periods are included without extrapolation; enable exclude partial for complete periods only.')
        if p['metric']=='sessions':
            warnings.append(f'Estimated session starts: gap greater than {p["session_gap"]} minutes or consecutive non-empty callsign change. Carry-in sessions are excluded; these are not verified airline flights.')
        if any(p[name] for name in ('category','aircraft_type','operator','icao','callsign')):
            warnings.append('Peak concurrent is receiver-wide: Aerodrome minute totals cannot be filtered by aircraft metadata.')
        if p['metric']=='aircraft':
            warnings.append('Unique aircraft are distinct within each displayed period. Bucket and type totals may overlap; the overall total is independently deduplicated.')
        if p['stat']=='average' and group in ('week','month','year'):
            warnings.append('Calendar-period average is the mean of daily counts within each period, not a divided distinct-aircraft total.')
        summary={'aircraft':len(total['aircraft']),'sessions':total['sessions'],'observations':total['observations'],
                 'peak_concurrent':max((n for _,_,n in minute_rows if n is not None),default=None),
                 'operator_known_pct':round(len(operator_known)/len(total['aircraft'])*100,1) if total['aircraft'] else 0}
        # Heatmap always uses hourly counts, independent of chart granularity.
        # If chart base is coarser, computing a heatmap from that would invent
        # time-of-day data. Fetch the bounded hourly projection only as needed.
        heatmap=[]
        if base in ('hour','minute'):
            hour_periods = {floor_timestamp(t,'hour') for t in eligible if coverage_seconds[t]>0 or bucket_metrics[t]['observations']}
            heat_samples = defaultdict(list)
            for t in hour_periods:
                local = datetime.fromtimestamp(t,LONDON)
                heat_samples[local.weekday(),local.hour].append(count(heat[t]))
            for day in range(7):
                for hour in range(24):
                    values=heat_samples[day,hour]
                    heatmap.append({'day':day,'hour':hour,'value':round(sum(values)/len(values),3) if values else None,'samples':len(values)})
        else:
            warnings.append('Time-of-day heatmap requires hourly grouping; select hour or hour of day to inspect it.')
        return {'metric':p['metric'],'group':group,'stat':p['stat'],'start':p['start'],'end':p['end'],
                'total':count(total),'summary':summary,'series':series,
                'breakdowns':{name:sorted([{'name':key,'value':count(value)} for key,value in values.items()],key=lambda r:(-r['value'],r['name']))[:100] for name,values in breakdown.items()},
                'heatmap':heatmap,'coverage':{'pct':round(coverage_pct,2),'active_seconds':int(active_seconds),'requested_seconds':int(req_seconds),'method':method,'warnings':warnings[:1] if coverage_pct<99.9 else []},
                'warnings':warnings + ['Aircraft categories and operators use current Aerodrome metadata; historical operator changes may not be reflected. Heatmap values are average counts per recorded hour.']}

    def compare(self, params: dict) -> dict:
        p=params_normalized(params)
        b=dict(p,start=integer(params.get('start_b'),p['start']-(p['end']-p['start']),0,7258118400),
               end=integer(params.get('end_b'),p['start'],0,7258118400))
        a_result=self.query(p)
        b_result=self.query(b)
        warnings=[]
        equal=abs((p['end']-p['start'])-(b['end']-b['start']))<=1
        comparable=equal and a_result['coverage']['pct']>=95 and b_result['coverage']['pct']>=95
        if not equal:
            warnings.append('Selected periods have different elapsed durations. Raw totals are not directly comparable.')
        if min(a_result['coverage']['pct'],b_result['coverage']['pct'])<95:
            warnings.append('Insufficient history or collection coverage for a reliable comparison.')
        delta=a_result['total']-b_result['total']
        percent=round(delta/b_result['total']*100,2) if b_result['total'] and comparable else None
        return {'a':a_result,'b':b_result,'delta':delta if comparable else None,'percent_change':percent,'comparable':comparable,'warnings':warnings}

    def tables(self) -> dict:
        with self.db.connect() as con:
            available=self.db.available(con)
            return {'tables':[{'name':name,'columns':self.db.columns(con,name),'technical':name in ('_aerodrome_meta','schema_version')} for name in sorted(TABLES & available)]}

    def rows(self, params: dict) -> dict:
        table=str(params.get('table') or 'all_sightings')
        if table not in TABLES:
            raise ValueError('That table is not available in the read-only browser.')
        limit=integer(params.get('limit'),50,1,10000)
        offset=integer(params.get('offset'),0,0,1000000)
        search=str(params.get('search') or '').strip()
        if len(search)>100:
            raise ValueError('Search is limited to 100 characters.')
        with self.db.connect() as con:
            if table not in self.db.available(con):
                raise ValueError('Table does not exist in this Aerodrome database.')
            columns=self.db.columns(con,table)
            names=[r['name'] for r in columns]
            where=[]
            values=[]
            if search:
                searchable=[name for name in names if name in ('icao','callsign','registration','aircraft_type','operator','country','category','origin_icao','dest_icao','airline','last_callsign','type_desc','special_label')]
                if not searchable:
                    raise ValueError('This technical table does not support text search.')
                where.append('('+' OR '.join(f'CAST("{name}" AS TEXT) LIKE ? ESCAPE \'\\\'' for name in searchable)+')')
                literal=search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                values += ['%'+literal+'%']*len(searchable)
            timecol=next((name for name in ('seen_at','hour_bucket','minute_bucket','day_bucket','last_seen_at','looked_up_at','resolved_at','ts') if name in names),None)
            for bound,operator in [('start','>='),('end','<')]:
                if params.get(bound) not in (None,'') and timecol:
                    where.append(f'"{timecol}" {operator} ?')
                    values.append(integer(params[bound],0,0,7258118400))
            sort=str(params.get('sort') or timecol or names[0])
            if sort not in names:
                raise ValueError('Unknown sort column.')
            direction=str(params.get('direction') or 'desc').lower()
            if direction not in ('asc','desc'):
                raise ValueError('Sort direction must be asc or desc.')
            clause=' WHERE '+' AND '.join(where) if where else ''
            total=con.execute(f'SELECT COUNT(*) FROM "{table}"'+clause,values).fetchone()[0]
            result=con.execute(f'SELECT * FROM "{table}"'+clause+f' ORDER BY "{sort}" {direction} LIMIT ? OFFSET ?',values+[limit,offset]).fetchall()
            return {'columns':names,'rows':[{k:(v.hex() if isinstance(v,bytes) else v) for k,v in dict(row).items()} for row in result],'total':total,'limit':limit,'offset':offset}

    def aircraft(self, icao: str, start=None, end=None) -> dict:
        if not re.fullmatch(r'[0-9a-fA-F]{6}',icao):
            raise ValueError('ICAO must contain exactly six hexadecimal characters.')
        now=int(time.time())
        start=integer(start,now-86400,0,7258118400)
        end=integer(end,now,0,7258118400)
        if end<=start:
            raise ValueError('Aircraft detail end must follow start.')
        with self.db.connect() as con:
            meta=con.execute('SELECT * FROM seen_aircraft WHERE icao IN (?,?) LIMIT 1',(icao.upper(),icao.lower())).fetchone() if 'seen_aircraft' in self.db.available(con) else None
            rows=con.execute('SELECT * FROM all_sightings WHERE icao IN (?,?) AND seen_at>=? AND seen_at<? ORDER BY seen_at LIMIT 1000',(icao.upper(),icao.lower(),start,end)).fetchall()
            total=con.execute('SELECT COUNT(*) FROM all_sightings WHERE icao IN (?,?) AND seen_at>=? AND seen_at<?',(icao.upper(),icao.lower(),start,end)).fetchone()[0]
        return {'metadata':dict(meta) if meta else {'icao':icao.upper()},'observations':[dict(row) for row in rows],'total':total}
