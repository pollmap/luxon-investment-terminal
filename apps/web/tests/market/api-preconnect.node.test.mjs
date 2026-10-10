import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';

// Local render fixtures only: no browser, DNS, API or provider calls.
const require=createRequire(import.meta.url);
const compile=source=>ts.transpileModule(source,{compilerOptions:{
  module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,
}}).outputText;
const transport={exports:{},URL};
vm.runInNewContext(compile(fs.readFileSync(new URL('../../components/market/public-transport.ts',import.meta.url),'utf8')),transport);
const {PUBLIC_ORIGIN}=transport.exports;
const layout=compile(fs.readFileSync(new URL('../../app/layout.tsx',import.meta.url),'utf8'));
function render(env={},origin=PUBLIC_ORIGIN){
  const context={exports:{},URL,process:{env},require:name=>{
    if(name==='./styles.css')return {};
    if(name==='../components/market/public-transport')return {PUBLIC_ORIGIN:origin};
    assert.equal(name,'react/jsx-runtime');
    return require(name);
  }};
  vm.runInNewContext(layout,context);
  return renderToStaticMarkup(context.exports.default({children:'fixture_non_production'}));
}

test('static HTML warms exactly the existing anonymous public API origin',()=>{
  const html=render({MARKET_WEB_OUTPUT:'static'});
  const hints=html.match(/<link\b[^>]*rel="preconnect"[^>]*>/g)||[];
  assert.equal(hints.length,1);
  assert.ok(hints[0].includes(`href="${PUBLIC_ORIGIN}"`));
  assert.match(hints[0],/crossorigin="anonymous"/);
  assert.ok(html.indexOf(hints[0])<html.indexOf('<body>'));
  assert.match(html,/<body>fixture_non_production<\/body>/);
});

test('standalone and development rendering add no preconnect',()=>{
  for(const mode of [undefined,'standalone','development','STATIC']){
    assert.doesNotMatch(render({MARKET_WEB_OUTPUT:mode}),/rel="preconnect"/);
  }
});

test('private or credential-bearing environment settings never select a connection target',()=>{
  const expected=render({MARKET_WEB_OUTPUT:'static'});
  for(const privateValue of ['http://127.0.0.1:8210','http://10.0.0.2:8210','https://fixture-user:fixture-secret@example.invalid']){
    const html=render({MARKET_WEB_OUTPUT:'static',MARKET_API_BASE_URL:privateValue,
      NEXT_PUBLIC_MARKET_API_URL:privateValue,NEXT_PUBLIC_SITE_URL:privateValue});
    assert.equal(html,expected);
    assert.ok(!html.includes(privateValue));
  }
});

test('non-HTTP or non-origin transport constants cannot emit a connection hint',()=>{
  for(const invalid of ['ftp://example.invalid','https://fixture-user:fixture-secret@example.invalid',
    'https://example.invalid/api','https://example.invalid?fixture=secret','https://example.invalid#private']){
    assert.doesNotMatch(render({MARKET_WEB_OUTPUT:'static'},invalid),/rel="preconnect"/);
  }
});
