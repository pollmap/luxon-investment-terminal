import https from 'node:https';
import fs from 'node:fs/promises';
import {performance} from 'node:perf_hooks';

const host='stocks.62-171-141-206.sslip.io';
const local=process.env.MARKET_LOAD_LOOPBACK==='1';
const agent=new https.Agent({keepAlive:true,maxSockets:100});
function read(path) { return new Promise(resolve=>{
  const start=performance.now();let bytes=0;
  const req=https.get({hostname:local?'127.0.0.1':host,port:443,servername:host,path,
    headers:{Host:host,'Accept-Encoding':'gzip'},agent},res=>{
    res.on('data',b=>bytes+=b.length);
    res.on('end',()=>resolve({ms:performance.now()-start,status:res.statusCode,bytes}));
    res.on('error',()=>resolve({ms:performance.now()-start,status:0,bytes}));
  });
  req.on('error',()=>resolve({ms:performance.now()-start,status:0,bytes}));
  req.setTimeout(30000,()=>req.destroy());
});}
const report={measured_at:new Date().toISOString(),concurrency:100,requests_per_endpoint:300,
  path:local?'VPS loopback HTTPS Nginx public route; excludes WAN':'PC HTTPS public domain; includes WAN',
  warmup:'100 simultaneous connections per endpoint; warm caches and TLS connections'};
for(const [name,suffix] of [['search','/search?q=%EC%82%BC%EC%84%B1'],['summary','/securities/005930?view=summary']]) {
 const path='/api/market/v1'+suffix;
 const warm=await Promise.all(Array.from({length:100},()=>read(path)));
 const rows=[];
 await Promise.all(Array.from({length:100},async()=>{for(let n=0;n<3;n++) rows.push(await read(path));}));
 const sorted=rows.map(r=>r.ms).sort((a,b)=>a-b);
 report[name]={p50_ms:+sorted[149].toFixed(2),p95_ms:+sorted[284].toFixed(2),max_ms:+sorted.at(-1).toFixed(2),
   errors:rows.filter(r=>r.status!==200).length,warmup_errors:warm.filter(r=>r.status!==200).length};
}
agent.destroy();
if(process.env.MARKET_LOAD_OUTPUT) await fs.writeFile(process.env.MARKET_LOAD_OUTPUT,JSON.stringify(report,null,2));
console.log(JSON.stringify(report));
