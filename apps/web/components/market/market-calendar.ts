// Toss calendar dates belong to the market; displayed instants are always KST.
export type CalendarSession = {startTime?:string; endTime?:string};
export type CalendarDay = {date:string; integrated?:Record<string,CalendarSession|null>|null} & Record<string,unknown>;
export type CalendarSnapshot = {status?:string; calendar?:{today?:CalendarDay; nextBusinessDay?:CalendarDay}};

const sessions = [
  ["dayMarket", "주간거래"], ["preMarket", "프리마켓"],
  ["regularMarket", "정규장"], ["afterMarket", "애프터마켓"],
] as const;
const seoul = new Intl.DateTimeFormat("en-GB", {
  timeZone:"Asia/Seoul", year:"numeric", month:"2-digit", day:"2-digit",
  hour:"2-digit", minute:"2-digit", hourCycle:"h23",
});

export function formatCalendarTime(value:unknown, marketDate:string):string|null {
  // Offset-free strings depend on the viewer's timezone and must not be guessed.
  if(typeof value!=="string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const instant=new Date(value);
  if(!Number.isFinite(instant.getTime())) return null;
  const sourceDay=value.substring(0,10);
  if(new Date(`${sourceDay}T00:00:00Z`).toISOString().substring(0,10)!==sourceDay || Number(value.substring(11,13))>23) return null;
  const parts=Object.fromEntries(seoul.formatToParts(instant).map(part=>[part.type,part.value]));
  const day=`${parts.year}-${parts.month}-${parts.day}`, time=`${parts.hour}:${parts.minute}`;
  if(day===marketDate) return time;
  return `${parts.year===marketDate.substring(0,4)?`${parts.month}/${parts.day}`:day} ${time}`;
}

export function calendarSessions(day:CalendarDay|undefined, country:"KR"|"US") {
  const result:{key:string; label:string; time:string}[]=[];
  // Current official KR schema supplies integrated only, never separate KRX/NXT hours.
  const source=country==="KR"?day?.integrated:day;
  if(source===null) return {status:"closed" as const,rows:result};
  if(!source || typeof source!=="object") return {status:"unavailable" as const,rows:result};
  const keys=sessions.filter(([key])=>country==="US" || key!=="dayMarket");
  let complete=true;
  for(const [key,label] of keys) {
    const raw=source[key];
    if(raw===null) continue;
    if(!raw || typeof raw!=="object") {complete=false;continue;}
    const session=raw as CalendarSession;
    const start=formatCalendarTime(session.startTime,day?.date||""),end=formatCalendarTime(session.endTime,day?.date||"");
    const ordered=typeof session.startTime==="string" && typeof session.endTime==="string" && Date.parse(session.endTime)>=Date.parse(session.startTime);
    result.push({key,label,time:start&&end&&ordered?`${start}–${end}`:"시간 확인 불가"});
  }
  return {status:result.length?"ready" as const:complete?"closed" as const:"unavailable" as const,rows:result};
}
