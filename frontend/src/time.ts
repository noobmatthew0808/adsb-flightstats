import type {Filters,Status} from './types';
export const zone='Europe/London';
export const dateTime=(n:number|null|undefined)=>n?new Intl.DateTimeFormat('en-GB',{timeZone:zone,dateStyle:'medium',timeStyle:'short'}).format(new Date(n*1000)):'Unavailable';
export const number=(n:number|null|undefined)=>n==null?'—':new Intl.NumberFormat('en-GB',{maximumFractionDigits:1}).format(n);
export function londonInput(epoch=Date.now()/1000){const p=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(epoch*1000));const v=(k:string)=>p.find(x=>x.type===k)?.value;return `${v('year')}-${v('month')}-${v('day')}T${v('hour')}:${v('minute')}`;}
export function londonEpoch(value:string,fold:'earlier'|'later'='earlier'){if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(value))throw Error('Choose a valid London date and time.');const base=Date.parse(value+'Z')/1000;for(const n of (fold==='later'?[base+3600,base,base-3600]:[base-3600,base,base+3600]))if(londonInput(n)===value)return n;throw Error('That London time does not exist during the spring clock change. Choose another time.');}
function localStart(now:number,kind:string){const d=londonInput(now).slice(0,10);let [year,month,day]=d.split('-').map(Number);if(kind==='year'){month=1;day=1;}if(kind==='month')day=1;const date=new Date(Date.UTC(year,month-1,day));if(kind==='week'){const dow=(date.getUTCDay()+6)%7;date.setUTCDate(date.getUTCDate()-dow);}return londonEpoch(date.toISOString().slice(0,10)+'T00:00');}
export function windowFor(f:Filters,status?:Status|null,now=Date.now()/1000):[number,number]{
 let end=Math.floor(now),start=end-86400;
 if(f.preset==='custom'){start=londonEpoch(f.start,f.fold);end=londonEpoch(f.end,f.fold);}
 else if(f.preset==='rolling'){const multiplier:Record<string,number>={hours:3600,days:86400,weeks:604800};start=end-Number(f.duration)*(multiplier[f.unit]||3600);}
 else if(f.preset==='all'){start=status?.source.first_seen||end-7*86400;}
 else if(f.preset==='today')start=localStart(end,'day');
 else if(f.preset==='yesterday'){end=localStart(end,'day');const d=new Date(Date.parse(londonInput(end).slice(0,10)+'T00:00:00Z')-86400000);start=londonEpoch(d.toISOString().slice(0,10)+'T00:00');}
 else if(['week','month','year'].includes(f.preset))start=localStart(end,f.preset);
 else start=end-Number(f.preset)*3600;
 if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end)throw Error('The start must be before the end, and the duration must be positive.');
 return [Math.floor(start),Math.floor(end)];
}
export function filterParams(f:Filters,status?:Status|null){const [start,end]=windowFor(f,status);return {start,end,metric:f.metric,group:f.group,stat:f.stat,category:f.category,aircraft_type:f.aircraft_type,operator:f.operator,icao:f.icao,callsign:f.callsign,month:f.month,exclude_partial:f.exclude_partial,session_gap:f.session_gap};}
export const metricName={aircraft:'Unique aircraft',sessions:'Estimated sessions',observations:'Recorded observations'};
export const metricHelp={aircraft:'Distinct ICAO identities in the selected window. The same aircraft can appear in multiple buckets, so bucket counts are not additive.',sessions:'Estimated observed sessions starting in this window, split by the configured idle gap or a change between known callsigns. These are not confirmed airline flights.',observations:'Sampled aircraft snapshots recorded by Aerodrome. These are not individual radio messages or flights.'};
