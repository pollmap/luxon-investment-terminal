// Source fiscal-year labels are not publication dates or a December year end.
export function annualPeriodLabel(period:string):string {
  const match=/^(\d{4})(?:[./-](0[1-9]|1[0-2])(?:[./-](0[1-9]|[12]\d|3[01]))?)?$/.exec(period);
  return match?match[1]:period;
}

export function earningsPeriodWindow(observed:string[],frequency:string,count:number){
  const all=[...new Set(observed)].sort();
  const annualYears=frequency==='annual'&&all.length>0&&all.every(p=>/^\d{4}$/.test(p));
  const anchor=annualYears?all.at(-1)!:null;
  // With no source calendar/as-of contract, end at the last reported fiscal
  // year. Never use the browser year to imply an unpublished period is due.
  const periods=annualYears&&count>0&&count<=10
    ?Array.from({length:count},(_,i)=>String(Number(anchor)-count+1+i))
    :count>0?all.slice(-count):all;
  return {periods,anchor,annualYears,missing:periods.filter(p=>!all.includes(p))};
}

export function observedFinancialValue(value:number|null|undefined):number|null {
  return typeof value==='number'&&Number.isFinite(value)?value:null;
}

type ComparedPoint={date:string;comparison_eligible:boolean;reference_price:number|null};
export function comparisonWindow(points:ComparedPoint[]){
  const valid=(p:ComparedPoint)=>p.comparison_eligible===true
    &&typeof p.reference_price==='number'&&Number.isFinite(p.reference_price);
  const first=points.findIndex(valid);
  if(first<0)return null;
  let last=first,gaps=false;
  for(let i=first+1;i<points.length;i++)if(valid(points[i]))last=i;
  for(let i=first;i<=last;i++)if(!valid(points[i]))gaps=true;
  return {start:points[first].date,end:points[last].date,gaps};
}
