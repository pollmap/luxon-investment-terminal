"use client";
import {useEffect, useRef} from "react";
import {createChart, LineSeries, CandlestickSeries, BarSeries, HistogramSeries, AreaSeries, LineStyle, LineType, ColorType, type Time, type UTCTimestamp, type ISeriesApi, type IChartApi, type SeriesType} from "lightweight-charts";

export type ChartRow = {time:string;close:number;open?:number;high?:number;low?:number;volume?:number|null;ma20?:number|null;valid?:boolean};
export type ComparisonLine={values:{time:string;value:number|null}[];color:string;area?:boolean;dashed?:boolean;step?:boolean};
const chartTime=(time:string):Time=>{
  if(/^\d{8,14}$/.test(time))time=time.slice(0,4)+'-'+time.slice(4,6)+'-'+time.slice(6,8)+'T'+(time.slice(8,10)||'00')+':'+(time.slice(10,12)||'00')+':'+(time.slice(12,14)||'00')+'+09:00';
  return time.length===10?time:Math.floor(Date.parse(time)/1000) as UTCTimestamp;
};
const timeKey=(time:Time)=>typeof time==='object'?time.year+'-'+String(time.month).padStart(2,'0')+'-'+String(time.day).padStart(2,'0'):String(time);
type Overlay={series:ISeriesApi<'Line'>|ISeriesApi<'Area'>;definition:ComparisonLine};
type Runtime={chart:IChartApi;price:ISeriesApi<SeriesType>;kind:string;volume:ISeriesApi<'Histogram'>;average:ISeriesApi<'Line'>;overlays:Overlay[];rangeKey:string;times:string[];indices:Map<string,number>};
function addPrice(chart:IChartApi,kind:string):ISeriesApi<SeriesType>{
  return kind==='candle'?chart.addSeries(CandlestickSeries,{borderVisible:false}):kind==='bar'?chart.addSeries(BarSeries):kind==='area'?chart.addSeries(AreaSeries,{lineWidth:2,priceLineVisible:false}):chart.addSeries(LineSeries,{lineWidth:2,priceLineVisible:false});
}

export default function FinancialChart({rows,kind="line",rangeKey="",movingAverage=false,comparisons,breakGaps=false,onSelect,label="주가 차트"}:{rows:ChartRow[];kind?:string;rangeKey?:string;movingAverage?:boolean;comparisons?:ComparisonLine[];breakGaps?:boolean;onSelect?:(index:number)=>void;label?:string}){
  const container=useRef<HTMLDivElement>(null), select=useRef(onSelect), runtime=useRef<Runtime|null>(null);
  useEffect(()=>{select.current=onSelect;},[onSelect]);
  useEffect(()=>{
    if(!container.current)return;
    const host=container.current, app=host.closest('.mk-app');
    const css=(key:string)=>getComputedStyle(host).getPropertyValue(key).trim();
    const chart=createChart(host,{autoSize:true,height:420,layout:{background:{type:ColorType.Solid,color:'transparent'},textColor:css('--mk-muted'),fontFamily:'Pretendard, sans-serif',fontSize:12,attributionLogo:true},grid:{vertLines:{visible:false},horzLines:{color:css('--mk-line')}},rightPriceScale:{borderVisible:false,scaleMargins:{top:.08,bottom:.24}},timeScale:{borderVisible:false,secondsVisible:false,tickMarkFormatter:(t:Time)=>typeof t==='number'?new Date(t*1000).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}):null},localization:{locale:'ko-KR',timeFormatter:(t:Time)=>typeof t==='number'?new Date(t*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):timeKey(t),priceFormatter:(v:number)=>v.toLocaleString('ko-KR',{maximumFractionDigits:2})},handleScroll:{vertTouchDrag:false}});
    const price=addPrice(chart,'line');
    const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume',priceLineVisible:false,lastValueVisible:false});
    volume.priceScale().applyOptions({scaleMargins:{top:.82,bottom:0}});
    const average=chart.addSeries(LineSeries,{lineWidth:1,priceLineVisible:false,lastValueVisible:false});
    const state:Runtime={chart,price,kind:'line',volume,average,overlays:[],rangeKey:'',times:[],indices:new Map()};
    runtime.current=state;
    chart.subscribeCrosshairMove(event=>{if(event.time&&event.sourceEvent){const index=state.indices.get(timeKey(event.time));if(index!==undefined)select.current?.(index);}});
    const repaint=()=>{
      if(state.kind==='candle'||state.kind==='bar')state.price.applyOptions({upColor:css('--mk-up'),downColor:css('--mk-down'),wickUpColor:css('--mk-up'),wickDownColor:css('--mk-down')});
      else if(state.kind==='area')state.price.applyOptions({lineColor:css('--mk-accent'),topColor:css('--mk-accent')+'18',bottomColor:css('--mk-accent')+'18'});
      else state.price.applyOptions({color:css('--mk-accent')});
      average.applyOptions({color:css('--mk-amber')||'#b68923'});
      for(const {series,definition} of state.overlays){const c=css(definition.color)||definition.color;series.applyOptions(definition.area?{lineColor:c,topColor:c+'18',bottomColor:c+'18'}:{color:c});}
      chart.applyOptions({layout:{textColor:css('--mk-muted')},grid:{horzLines:{color:css('--mk-line')}}});
    };
    repaint();
    const observer=new MutationObserver(repaint);if(app)observer.observe(app,{attributes:true,attributeFilter:['data-theme','class']});
    const media=matchMedia('(prefers-color-scheme: dark)');media.addEventListener('change',repaint);
    return()=>{observer.disconnect();media.removeEventListener('change',repaint);runtime.current=null;chart.remove();};
  },[]);

  useEffect(()=>{
    const state=runtime.current,host=container.current;if(!state||!host)return;
    const {chart,volume,average}=state;
    const css=(key:string)=>getComputedStyle(host).getPropertyValue(key).trim();
    const previousRange=chart.timeScale().getVisibleLogicalRange();
    if(state.kind!==kind){chart.removeSeries(state.price);state.price=addPrice(chart,kind);state.kind=kind;}
    const price=state.price;
    if(kind==='candle'||kind==='bar')price.applyOptions({upColor:css('--mk-up'),downColor:css('--mk-down'),wickUpColor:css('--mk-up'),wickDownColor:css('--mk-down')});
    else if(kind==='area')price.applyOptions({lineColor:css('--mk-accent'),topColor:css('--mk-accent')+'18',bottomColor:css('--mk-accent')+'18'});
    else price.applyOptions({color:css('--mk-accent')});
    const timed=rows.map((r,index)=>({...r,index,time:chartTime(r.time)}));
    state.indices=new Map(timed.map(r=>[timeKey(r.time),r.index]));
    chart.applyOptions({timeScale:{timeVisible:rows.some(r=>r.time.length>10)}});
    const segmented=breakGaps&&kind==='line';
    price.setData(kind==='candle'||kind==='bar'
      ? timed.map(r=>r.valid!==false&&[r.open,r.high,r.low,r.close].every(v=>Number.isFinite(v))?{time:r.time,open:r.open!,high:r.high!,low:r.low!,close:r.close}:{time:r.time})
      : timed.map(r=>!segmented&&r.valid!==false&&Number.isFinite(r.close)?{time:r.time,value:r.close}:{time:r.time}));
    volume.setData(timed.filter(r=>r.volume!=null&&Number.isFinite(r.volume)).map(r=>({time:r.time,value:r.volume!,color:r.close>=(r.open??r.close)?css('--mk-up')+'66':css('--mk-down')+'66'})));
    average.setData(movingAverage?timed.map(r=>r.ma20!=null?{time:r.time,value:r.ma20}:{time:r.time}):[]);
    let overlayIndex=0;
    // Whitespace retains the time/index anchor, but a line library may join
    // observations across it. Separate series make every invalid gap explicit.
    const definitions:ComparisonLine[]=segmented?[{values:rows.map(r=>({time:r.time,value:r.valid!==false&&Number.isFinite(r.close)?r.close:null})),color:'--mk-accent',step:false},...(comparisons??[])]:comparisons??[];
    for(const definition of definitions){
      let segment:{time:Time;value:number}[]=[];
      const flush=()=>{
        if(!segment.length)return;const color=css(definition.color)||definition.color;
        const options={lineWidth:2 as const,lineType:definition.step===false?LineType.Simple:LineType.WithSteps,priceLineVisible:false,lastValueVisible:false};
        const style=definition.area?{...options,lineColor:color,topColor:color+'18',bottomColor:color+'18'}:{...options,color,lineStyle:definition.dashed?LineStyle.Dashed:LineStyle.Solid};
        let overlay:Overlay|undefined=state.overlays[overlayIndex];
        if(overlay&&Boolean(overlay.definition.area)!==Boolean(definition.area)){
          chart.removeSeries(overlay.series);overlay=undefined;
        }
        if(!overlay){
          const series=definition.area?chart.addSeries(AreaSeries,style):chart.addSeries(LineSeries,style);
          overlay={series,definition};state.overlays[overlayIndex]=overlay;
        }else{overlay.series.applyOptions(style);overlay.definition=definition;}
        overlay.series.setData(segment);overlayIndex++;segment=[];
      };
      for(const point of definition.values){if(point.value==null||!Number.isFinite(point.value)){flush();continue;}segment.push({time:chartTime(point.time),value:point.value});}flush();
    }
    for(const overlay of state.overlays.splice(overlayIndex))chart.removeSeries(overlay.series);
    // Append/prepend updates and shape changes must not reset a user's zoom.
    // Explicit period/identity changes are supplied separately by the caller.
    const anchor=previousRange?Math.max(0,Math.min(state.times.length-1,Math.floor(previousRange.from))):0;
    const nextAnchor=state.times.length?rows.findIndex(row=>row.time===state.times[anchor]):-1;
    if(rows.length&&(!state.times.length||rangeKey!==state.rangeKey||nextAnchor<0))chart.timeScale().fitContent();
    else if(previousRange){const offset=nextAnchor-anchor;chart.timeScale().setVisibleLogicalRange({from:previousRange.from+offset,to:previousRange.to+offset});}
    state.times=rows.map(row=>row.time);
    state.rangeKey=rangeKey;
  },[rows,kind,rangeKey,movingAverage,comparisons,breakGaps]);
  return <div ref={container} className="mk-financial-canvas" role="img" aria-label={label} style={{height:'var(--mk-chart-height, 420px)',width:'100%',minWidth:0}}/>;
}

