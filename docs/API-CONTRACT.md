# Internal API contract
All timestamps are Unix seconds UTC. Display timezone Europe/London. All errors JSON {detail:string}. GETs read-only. JSON POST mutations require authenticated admin cookie and X-CSRF-Token. Login POST /api/auth/login {password}; response {csrf_token}; GET /api/auth/session => {authenticated,csrf_token}; POST /api/auth/logout.

GET /api/status => {version,demo,source:{connected,path,error,first_seen,last_seen,observations,aircraft,bytes},timezone,session_gap_minutes}
GET /api/analytics?start=<epoch>&end=<epoch>&metric=aircraft|sessions|observations&group=hour|day|week|month|year|weekday|hour_of_day|month_of_year&stat=count|average|cumulative|moving_average&category=&aircraft_type=&operator=&icao=&callsign=&month=<1..12>&exclude_partial=false&session_gap=30
=> {metric,group,stat,start,end,total,summary:{aircraft,sessions,observations,peak_concurrent,operator_known_pct},series:[{key,label,start,end,value,count,coverage_pct,samples}],breakdowns:{types:[{name,value}],operators:[{name,value}],categories:[{name,value}]},heatmap:[{day,hour,value,samples}],coverage:{pct,active_seconds,requested_seconds,method,warnings:[]},warnings:[]}
Totals distinct over entire interval, not sum of bucket uniques. Average groups weekday/hour_of_day/month_of_year average constituent day/hour/month counts with measured collection coverage. Sessions count session starts; carry-in data used for boundary correctness.
GET /api/compare with same filters plus start_b,end_b => {a:<analytics>,b:<analytics>,delta,percent_change,comparable,warnings:[]}
GET /api/data/tables => {tables:[{name,columns:[{name,type}],technical:boolean}]}
GET /api/data/rows?table=all_sightings&search=&limit=50&offset=0&start=&end=&sort=&direction=desc => {columns:[],rows:[],total,limit,offset}
GET /api/data/export same filters => CSV attachment (cap 10000 rows)
GET /api/analytics/export same filters => CSV
GET /api/aircraft/{icao}?start=&end= => {metadata:{},observations:[],total} bounded 1000 points
GET /api/settings => {services:[{id,label,description,url,icon}],theme:'dark'|'light'|'system',refresh_seconds:60,session_gap_minutes:30}
PUT /api/settings authenticated/CSRF => complete settings object; GET public.
GET /api/updates/status => {installed_version,available_version,status,progress,message,history:[],release_notes,configured,can_rollback}
POST /api/updates/check, /api/updates/install, /api/updates/rollback => same shape, authenticated/CSRF.
