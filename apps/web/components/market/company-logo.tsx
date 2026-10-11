"use client";
import {useState} from "react";
import {Building2} from "lucide-react";
import Image from "next/image";
export default function CompanyLogo({url,name,large=false}:{url?:string|null;name:string;large?:boolean}){
 const [failed,setFailed]=useState(false);
 return <span className={`mk-company-logo ${large?'mk-company-logo-large':''}`} aria-hidden="true">{url&&!failed?<Image src={url} alt="" title={name} width={large?56:32} height={large?56:32} unoptimized referrerPolicy="no-referrer" onError={()=>setFailed(true)}/>:<Building2 size={large?28:18}/>}</span>;
}
