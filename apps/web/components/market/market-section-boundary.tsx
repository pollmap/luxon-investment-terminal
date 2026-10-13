"use client";
import {Component,type ReactNode} from 'react';
export function isChunkFailure(error:unknown){return error instanceof Error&&/ChunkLoadError|Loading (?:CSS )?chunk|Failed to fetch dynamically imported module|Importing a module script failed/.test(`${error.name} ${error.message}`);}
export function claimChunkRecovery(storage:Pick<Storage,'getItem'|'setItem'>,release:string){
 const key=`market:chunk-recovery:${release}`;if(storage.getItem(key)==='1')return false;storage.setItem(key,'1');return true;
}
export default class MarketSectionBoundary extends Component<{children:ReactNode;label:string},{failed:boolean;chunk:boolean;retried:boolean;blocked:boolean}>{
 state={failed:false,chunk:false,retried:false,blocked:false};
 static getDerivedStateFromError(error:unknown){return {failed:true,chunk:isChunkFailure(error)};}
 retry=()=>{
  if(this.state.chunk){let claimed=false;try{claimed=claimChunkRecovery(sessionStorage,process.env.NEXT_PUBLIC_MARKET_RELEASE||'local');}catch{}if(!claimed){this.setState({blocked:true});return;}window.dispatchEvent(new Event('market-chunk-reload'));return;}
  this.setState({failed:false,retried:true});
 };
 render(){if(!this.state.failed)return this.props.children;return <div className="mk-section-failure" role="alert"><p>{this.props.label} 화면을 불러오지 못했어요.</p>{this.state.blocked?<p className="mk-caption">같은 오류가 계속돼요. 다른 정보는 계속 볼 수 있어요.</p>:(!this.state.retried||this.state.chunk)&&<button className="mk-button" onClick={this.retry}>{this.state.chunk?'새 화면 불러오기':'다시 시도'}</button>}</div>;}
}
