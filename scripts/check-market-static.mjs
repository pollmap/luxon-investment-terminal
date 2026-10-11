import assert from 'node:assert/strict';
import {readFile, stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../apps/web/out');
const manifest=JSON.parse(await readFile(path.join(root,'build-manifest.json'),'utf8'));
assert.equal(manifest.profile,'static');
assert.ok(manifest.files.length>0);
for(const file of manifest.files){
 const target=path.resolve(root,file.path);
 assert.ok(target.startsWith(root+path.sep));
 assert.ok(!/(^|\/)(?:\.env[^/]*|node_modules|\.git|api)(\/|$)/.test(file.path));
 const bytes=await readFile(target);
 assert.equal(bytes.length,file.bytes,file.path);
 assert.equal(createHash('sha256').update(bytes).digest('hex'),file.sha256,file.path);
}
for(const name of ['stocks.html','404.html','legacy.html','legacy-entry.mjs','legacy-routes.mjs','stocks/sw.js','stocks/offline.html','stocks/manifest.webmanifest'])
 assert.ok((await stat(path.join(root,name))).isFile(),name);
const html=await readFile(path.join(root,'stocks.html'),'utf8');
for(const match of html.matchAll(/(?:src|href)="(\/[^"?#]+)(?:[^"\s]*)?"/g)){
 const asset=match[1];
 if(asset.startsWith('/_next/')||/\.(?:css|js|png|svg|webmanifest|woff2)$/.test(asset))
  assert.ok((await stat(path.join(root,asset.slice(1)))).isFile(),asset);
}
assert.ok(!html.includes('C:\\Users\\'));
const pwa=JSON.parse(await readFile(path.join(root,'stocks/manifest.webmanifest'),'utf8'));
assert.equal(pwa.start_url,'/stocks');
console.log(JSON.stringify({release:manifest.release,files:manifest.files.length,bytes:manifest.bytes,integrity:'passed',html_assets:'passed',pwa:'passed'}));
