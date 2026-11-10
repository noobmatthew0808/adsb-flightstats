"""Explicit synthetic demo source. Never called for a missing real database."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
import random
import sqlite3
import time

SCHEMA = '''
CREATE TABLE all_sightings(id INTEGER PRIMARY KEY, icao TEXT, callsign TEXT, speed REAL, lat REAL, lon REAL, altitude REAL, aircraft_type TEXT, type_desc TEXT, seen_at INTEGER, squawk TEXT);
CREATE INDEX idx_all_seen_icao ON all_sightings(seen_at,icao);
CREATE INDEX idx_all_icao_seen ON all_sightings(icao,seen_at);
CREATE TABLE seen_aircraft(icao TEXT PRIMARY KEY, first_seen_at INTEGER, first_callsign TEXT, first_aircraft_type TEXT, registration TEXT, last_callsign TEXT, aircraft_type TEXT, aircraft_type_desc TEXT, operator TEXT, country TEXT, last_lat REAL, last_lon REAL, last_seen_at INTEGER, sighting_count INTEGER, fts_dirty INTEGER, last_distance REAL, category TEXT, registered_owner TEXT, manufacturer TEXT, best_track_seconds INTEGER);
CREATE TABLE concurrent_minute(minute_bucket INTEGER PRIMARY KEY, count INTEGER);
CREATE TABLE sightings_hourly(icao TEXT, hour_bucket INTEGER, callsign TEXT, aircraft_type TEXT, type_desc TEXT, sighting_count INTEGER, first_seen_at INTEGER, last_seen_at INTEGER, last_lat REAL, last_lon REAL, last_altitude REAL, last_speed REAL, min_altitude REAL, max_altitude REAL, max_speed REAL, last_squawk TEXT, min_nonzero_altitude REAL, PRIMARY KEY(icao,hour_bucket));
CREATE TABLE military_sightings(id INTEGER PRIMARY KEY, icao TEXT, callsign TEXT, speed REAL, lat REAL, lon REAL, altitude REAL, aircraft_type TEXT, type_desc TEXT, seen_at INTEGER, special_label TEXT, squawk TEXT);
CREATE TABLE aircraft_track_daily(icao TEXT, day_bucket INTEGER, callsign TEXT, aircraft_type TEXT, current_session_start INTEGER, current_session_last INTEGER, best_session_start INTEGER, best_session_end INTEGER, best_session_duration INTEGER);
CREATE TABLE flight_routes(fa_flight_id TEXT, callsign TEXT, registration TEXT, origin_icao TEXT, origin_name TEXT, dest_icao TEXT, dest_name TEXT, departed_at INTEGER, arrives_at INTEGER, looked_up_at INTEGER);
CREATE TABLE route_cache(callsign TEXT, origin_icao TEXT, origin_name TEXT, dest_icao TEXT, dest_name TEXT, airline TEXT, resolved_at INTEGER, last_outcome TEXT, hit_count INTEGER, airports_json TEXT);
'''


def create_demo(path: str | Path, now: int | None = None, days: int = 14) -> Path:
    """Create a reproducible 14-day synthetic fixture (~180k samples), exclusively.

    Existing files are preserved. This function cannot overwrite a receiver DB.
    """
    path=Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive filesystem creation protects existing files and symlinks.
    with path.open('xb'):
        pass
    rng=random.Random(8088)
    now=int(now or time.time())//60*60
    first=now-days*86400
    planes=[]
    types=[('A320','Airbus A320','commercial','easyJet'),('B738','Boeing 737-800','commercial','Ryanair'),('A21N','Airbus A321neo','commercial','British Airways'),('B789','Boeing 787-9','commercial','Virgin Atlantic'),('A359','Airbus A350-900','commercial',None),('C172','Cessna 172','general_aviation',None),('EUFI','Eurofighter Typhoon','military','Royal Air Force'),('EC35','Eurocopter EC135','helicopter',None)]
    for i in range(180):
        typ,desc,category,operator=types[i%len(types)]
        icao=f'{0x400000+i:06X}'
        prefix={'easyJet':'EZY','Ryanair':'RYR','British Airways':'BAW','Virgin Atlantic':'VIR','Royal Air Force':'RFR'}.get(operator,'G')
        planes.append((icao,f'{prefix}{100+i}',typ,desc,category,operator))
    try:
        con=sqlite3.connect(path)
        con.executescript(SCHEMA)
        data=[]
        minute_counts={}
        for day in range(days):
            for visit in range(130):
                plane=planes[(visit*7+day*19)%len(planes)]
                # Daytime traffic peak with some night observations.
                offset=int(rng.triangular(0,86400,13*3600))//10*10
                start=first+day*86400+offset
                length=rng.randint(30,150)
                for sample in range(length):
                    t=start+sample*10
                    if t>=now:
                        break
                    # An intentional short collection gap, visible as missing.
                    if first+5*86400+3600<=t<first+5*86400+4800:
                        continue
                    icao,callsign,typ,desc,category,operator=plane
                    altitude=round(1500+math.sin(sample/max(1,length-1)*math.pi)*33000)
                    lat=51.7+sample*0.003+rng.uniform(-0.1,0.1)
                    lon=-0.4+sample*0.004+rng.uniform(-0.1,0.1)
                    data.append((icao,callsign,round(rng.uniform(100,490)),lat,lon,altitude,typ,desc,t,'7000' if category=='general_aviation' else '1000'))
                    minute_counts.setdefault(t//60*60,set()).add(icao)
        con.executemany('INSERT INTO all_sightings(icao,callsign,speed,lat,lon,altitude,aircraft_type,type_desc,seen_at,squawk) VALUES(?,?,?,?,?,?,?,?,?,?)',data)
        for icao,callsign,typ,desc,category,operator in planes:
            mn,mx,count=con.execute('SELECT MIN(seen_at),MAX(seen_at),COUNT(*) FROM all_sightings WHERE icao=?',(icao,)).fetchone()
            con.execute('INSERT INTO seen_aircraft VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(icao,mn,callsign,typ,None,callsign,typ,desc,operator,'United Kingdom',51.8,-0.3,mx,count,0,40,category,operator,'Airbus' if typ.startswith('A') else None,1200))
        con.executemany('INSERT INTO concurrent_minute VALUES(?,?)',[(t,len(minute_counts.get(t,()))) for t in range(first,now,60) if not first+5*86400+3600<=t<first+5*86400+4800])
        con.execute('''INSERT INTO sightings_hourly SELECT icao,CAST(seen_at/3600 AS INTEGER)*3600,MAX(callsign),MAX(aircraft_type),MAX(type_desc),COUNT(*),MIN(seen_at),MAX(seen_at),MAX(lat),MAX(lon),MAX(altitude),MAX(speed),MIN(altitude),MAX(altitude),MAX(speed),MAX(squawk),MIN(NULLIF(altitude,0)) FROM all_sightings GROUP BY icao,2''')
        con.execute('''INSERT INTO military_sightings SELECT a.id,a.icao,a.callsign,a.speed,a.lat,a.lon,a.altitude,a.aircraft_type,a.type_desc,a.seen_at,'Synthetic military',a.squawk FROM all_sightings a JOIN seen_aircraft m USING(icao) WHERE m.category='military' ''')
        con.commit()
        con.close()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Create an explicitly synthetic demo database')
    parser.add_argument('path')
    args=parser.parse_args()
    print(create_demo(args.path))
