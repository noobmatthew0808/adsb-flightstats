from datetime import datetime, timezone
import sqlite3

import pytest

from backend.analytics import Analytics, floor_timestamp, next_timestamp
from backend.db import Database, DatabaseError
from backend.demo import SCHEMA, create_demo


def epoch(text):
    return int(datetime.fromisoformat(text).timestamp())


@pytest.fixture
def source(tmp_path):
    path=tmp_path/'history.db'
    con=sqlite3.connect(path)
    con.executescript(SCHEMA)
    yield path,con
    con.close()


def sighting(con,icao,t,callsign='BAW123',typ='A320'):
    con.execute('INSERT INTO all_sightings(icao,callsign,aircraft_type,seen_at) VALUES(?,?,?,?)',(icao,callsign,typ,t))
    con.commit()


def coverage(con,start,end,zero=False):
    con.executemany('INSERT OR IGNORE INTO concurrent_minute VALUES(?,?)',[(t,0 if zero else 1) for t in range(start,end,60)])
    con.commit()


def test_readonly_and_missing_database(source,tmp_path):
    path,con=source
    with Database(path).connect() as db:
        assert db.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):
            db.execute('DELETE FROM all_sightings')
    with pytest.raises(DatabaseError,match='not found'):
        with Database(tmp_path/'missing.db').connect():
            pass
    assert not (tmp_path/'missing.db').exists()


def test_unique_is_not_hourly_sum_and_sessions_carry_in(source):
    path,con=source
    start=epoch('2026-11-10T00:00:00+00:00')
    for t in [start-60,start+60,start+600,start+1200,start+1800,start+2400,start+3000,start+3600]:
        sighting(con,'400001',t)
    coverage(con,start,start+7200)
    data=Analytics(Database(path)).query({'start':start,'end':start+7200})
    assert data['total']==1
    assert sum(x['value'] for x in data['series'])==2
    assert data['summary']['sessions']==0
    assert data['summary']['observations']==7
    assert data['coverage']['pct']==100


def test_session_gap_and_callsign(source):
    path,con=source
    start=epoch('2026-11-10T00:00:00+00:00')
    sighting(con,'400001',start)
    sighting(con,'400001',start+10)
    sighting(con,'400001',start+20,'BAW456')
    sighting(con,'400001',start+1900,'BAW456')
    sighting(con,'400001',start+1910,None)
    sighting(con,'400001',start+1920,'BAW456')
    data=Analytics(Database(path)).query({'start':start,'end':start+3600,'metric':'sessions'})
    assert data['total']==3
    assert data['summary']['aircraft']==1


def test_dst_days_and_duplicate_hours(source):
    path,con=source
    spring=epoch('2026-03-29T00:00:00+00:00')
    autumn=epoch('2026-10-24T23:00:00+00:00')
    assert next_timestamp(spring,'day')-spring==23*3600
    assert next_timestamp(autumn,'day')-autumn==25*3600
    first=epoch('2026-10-25T00:30:00+00:00')
    second=epoch('2026-10-25T01:30:00+00:00')
    assert floor_timestamp(first,'hour')!=floor_timestamp(second,'hour')
    sighting(con,'400001',first)
    sighting(con,'400002',second)
    coverage(con,autumn,autumn+25*3600)
    result=Analytics(Database(path)).query({'start':autumn,'end':autumn+25*3600,'group':'hour'})
    assert len(result['series'])==25
    assert len(set(r['label'] for r in result['series']))==25
    assert result['coverage']['pct']==100


def test_gap_is_null_but_recorded_zero_is_zero(source):
    path,con=source
    start=epoch('2026-11-10T00:00:00+00:00')
    coverage(con,start,start+3600,zero=True)
    result=Analytics(Database(path)).query({'start':start,'end':start+7200})
    assert result['series'][0]['value']==0
    assert result['series'][1]['value'] is None
    assert result['coverage']['pct']==50
    assert result['warnings']


def test_weekday_mean_uses_present_days_and_zero_coverage(source):
    path,con=source
    start=epoch('2026-11-02T00:00:00+00:00') # Monday
    for icao in ['400001','400002']:
        sighting(con,icao,start+60)
    sighting(con,'400001',start+7*86400+60)
    coverage(con,start,start+86400)
    coverage(con,start+7*86400,start+8*86400)
    data=Analytics(Database(path)).query({'start':start,'end':start+14*86400,'group':'weekday','stat':'average'})
    monday=data['series'][0]
    assert monday['value']==1.5
    assert monday['samples']==2
    assert data['series'][1]['value'] is None


def test_exclude_partial_does_not_count_edges(source):
    path,con=source
    start=epoch('2026-11-10T00:00:00+00:00')
    sighting(con,'400001',start+60)
    sighting(con,'400002',start+86400+60)
    coverage(con,start,start+2*86400)
    data=Analytics(Database(path)).query({'start':start+30,'end':start+2*86400,'group':'day','exclude_partial':True})
    assert data['total']==1
    assert len(data['series'])==1


def test_filters_literal_and_browser_whitelisted(source):
    path,con=source
    start=epoch('2026-11-10T00:00:00+00:00')
    sighting(con,'400001',start,'BAW123')
    sighting(con,'400002',start,'BA_123')
    analytics=Analytics(Database(path))
    assert analytics.rows({'search':'BA_'})['total']==1
    assert analytics.query({'start':start,'end':start+60,'callsign':'BA_'})['total']==1
    with pytest.raises(ValueError):
        analytics.rows({'table':'all_sightings; DROP TABLE seen_aircraft'})
    with pytest.raises(ValueError):
        analytics.rows({'sort':'seen_at; DROP TABLE all_sightings'})
    assert con.execute('SELECT COUNT(*) FROM all_sightings').fetchone()[0]==2


def test_comparison_missing_history_is_not_percent(source):
    path,con=source
    start=epoch('2026-11-10T00:00:00+00:00')
    sighting(con,'400001',start)
    coverage(con,start,start+3600)
    result=Analytics(Database(path)).compare({'start':start,'end':start+3600,'start_b':start-3600,'end_b':start})
    assert result['comparable'] is False
    assert result['delta'] is None
    assert result['percent_change'] is None


def test_demo_is_explicit_and_preserves_existing(tmp_path):
    path=tmp_path/'demo.db'
    create_demo(path,now=epoch('2026-11-10T12:00:00+00:00'))
    con=sqlite3.connect(path)
    assert con.execute('SELECT COUNT(*) FROM all_sightings').fetchone()[0]>100000
    con.close()
    before=path.read_bytes()
    create_demo(path)
    assert path.read_bytes()==before


def test_month_filter_week_overlaps_previous_year(source):
    path,con=source
    start=epoch('2026-01-01T00:00:00+00:00')
    sighting(con,'400001',start+60)
    coverage(con,start,start+4*86400)
    result=Analytics(Database(path)).query({'start':start,'end':start+7*86400,'group':'week','month':1})
    assert result['total']==1
    assert result['series'][0]['count']==1
    assert result['series'][0]['start']<start


def test_january_across_year_coverage_denominator(source):
    path,con=source
    start=epoch('2026-01-01T00:00:00+00:00')
    end=epoch('2027-01-01T00:00:00+00:00')
    sighting(con,'400001',start+60)
    coverage(con,start,start+31*86400)
    result=Analytics(Database(path)).query({'start':start,'end':end,'group':'year','month':1})
    assert result['total']==1
    assert result['coverage']['pct']==100
    assert result['coverage']['requested_seconds']==31*86400


def test_hour_heatmap_is_mean_of_constituent_hours(source):
    path,con=source
    start=epoch('2026-11-02T00:00:00+00:00')
    sighting(con,'400001',start+60)
    sighting(con,'400002',start+120)
    sighting(con,'400001',start+7*86400+60)
    coverage(con,start,start+3600)
    coverage(con,start+7*86400,start+7*86400+3600)
    result=Analytics(Database(path)).query({'start':start,'end':start+8*86400,'group':'hour_of_day'})
    cell=next(r for r in result['heatmap'] if r['day']==0 and r['hour']==0)
    assert cell['value']==1.5
    assert cell['samples']==2


def test_case_insensitive_icao_identity(source):
    path,con=source
    start=epoch('2026-11-02T00:00:00+00:00')
    sighting(con,'abc123',start)
    sighting(con,'ABC123',start+10)
    result=Analytics(Database(path)).query({'start':start,'end':start+3600})
    assert result['total']==1
    assert result['summary']['sessions']==1


def test_categorical_hour_pattern_can_span_multiple_years(source):
    path,con=source
    start=epoch('2025-01-01T00:00:00+00:00')
    end=epoch('2027-01-01T00:00:00+00:00')
    sighting(con,'400001',start+60)
    coverage(con,start,start+3600)
    result=Analytics(Database(path)).query({'start':start,'end':end,'group':'hour_of_day','stat':'average'})
    assert result['total']==1
    assert len(result['series'])==24
    assert result['series'][0]['value']==1
