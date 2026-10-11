export function earningsChange(current:number|null|undefined,previous:number|null|undefined):{percent:number|null;label:string}{
 if(current==null||previous==null||!Number.isFinite(current)||!Number.isFinite(previous))return {percent:null,label:"—"};
 if(previous<0)return {percent:null,label:current>0?"흑자 전환":current<0?"적자 지속":"손익 0"};
 if(current<0)return {percent:null,label:"적자 전환"};
 if(previous===0)return {percent:null,label:"전기 0"};
 const percent=(current/previous-1)*100;
 return {percent,label:`${percent>0?"+":""}${percent.toFixed(1)}%`};
}
