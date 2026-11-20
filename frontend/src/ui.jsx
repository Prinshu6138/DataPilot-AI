import React from 'react';
import {ArrowDownToLine,ArrowRight,ChevronLeft,ChevronRight,Database,FileSpreadsheet,FileText,Info,LoaderCircle,X} from 'lucide-react';
import axios from 'axios';
export const api=axios.create({baseURL:'/api',headers:{'X-DataPilot':'1'}});
export const errorText=e=> typeof e?.response?.data?.detail==='string'?e.response.data.detail:e?.response?.status===422?'Check the values you entered and try again.':e?.message||'Something went wrong. Please try again.';
export const fmt=n=>n===undefined||n===null?'—':typeof n==='number'?new Intl.NumberFormat(undefined,{maximumFractionDigits:3}).format(n):String(n);
export const date=v=>new Date(v).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});
export const kb=n=>n>=1048576?`${(n/1048576).toFixed(1)} MB`:`${(n/1024).toFixed(1)} KB`;
export const initials=n=>n?.split(/\s+/).map(x=>x[0]).slice(0,2).join('').toUpperCase()||'DP';
export function Btn({children,icon:Icon,kind='secondary',className='',busy=false,disabled,...props}){return <button type="button" className={`btn ${kind} ${className}`} disabled={disabled||busy} {...props}>{busy?<LoaderCircle size={16} className="spin"/>:Icon?<Icon size={16}/>:null}{children}</button>}
export function PageTitle({tag,title,sub,actions}){return <div className="page-title"><div>{tag&&<div className="eyebrow"><i/>{tag}</div>}<h1>{title}</h1><p>{sub}</p></div>{actions&&<div className="actions">{actions}</div>}</div>}
export function PanelHead({title,sub,action}){return <div className="panel-head"><div><h2>{title}</h2>{sub&&<p>{sub}</p>}</div>{action}</div>}
export function Empty({title='Nothing here yet',children,icon:Icon=Database,action}){return <div className="empty"><div className="tile purple"><Icon size={22}/></div><h3>{title}</h3><p>{children}</p>{action}</div>}
export function Loading({label='Loading workspace…'}){return <div className="loading"><LoaderCircle className="spin" size={20}/>{label}</div>}
export function ErrorBox({error}){return error?<div className="error" role="alert"><Info size={17}/><span>{typeof error==='string'?error:errorText(error)}</span></div>:null}
export function FileIcon({type='csv',report=false}){const Icon=report?FileText:FileSpreadsheet;return <span className={`file-icon ${report?'purple':type==='xlsx'?'green':'blue'}`}><Icon size={17}/></span>}
export function Quality({value}){return <span className={`badge ${value>=97?'green':'amber'}`}><i/>{fmt(value)}%</span>}
export function Table({heads,children}){return <div className="table-scroll"><table><thead><tr>{heads.map((x,i)=><th key={i} scope="col">{x}</th>)}</tr></thead><tbody>{children}</tbody></table></div>}
export function FileName({name,report=false}){return <span className="file-name"><FileIcon type={name.split('.').at(-1).toLowerCase()} report={report}/><span title={name}>{name}</span></span>}
export function Preview({columns,rows}){return rows?.length?<Table heads={columns.map(c=>typeof c==='string'?c:c.name)}>{rows.map((row,i)=><tr key={i}>{row.map((c,j)=><td key={j}><span className={c===null?'null':''} title={c===null?'Missing':String(c)}>{c===null?'—':typeof c==='number'?fmt(c):String(c)}</span></td>)}</tr>)}</Table>:<Empty title="No matching records">Try a different filter or reset the search.</Empty>}
export function Pager({page,total,onPage}){const pages=Math.max(1,Math.ceil(total/15));return <div className="pager"><span>{fmt(total)} records · Page {page} of {pages}</span><div className="actions"><Btn aria-label="Previous page" icon={ChevronLeft} disabled={page<=1} onClick={()=>onPage(page-1)}/><Btn aria-label="Next page" icon={ChevronRight} disabled={page>=pages} onClick={()=>onPage(page+1)}/></div></div>}
export function Modal({title,children,onClose}){React.useEffect(()=>{const old=document.activeElement; const f=e=>{if(e.key==='Escape')onClose(); if(e.key==='Tab'){const list=[...document.querySelectorAll('.dialog button:not(:disabled),.dialog input,.dialog select,.dialog a[href]')];if(!list.length)return;if(e.shiftKey&&document.activeElement===list[0]){e.preventDefault();list.at(-1).focus()}else if(!e.shiftKey&&document.activeElement===list.at(-1)){e.preventDefault();list[0].focus()}}};(document.querySelector('.search-dialog input')||document.querySelector('.dialog button'))?.focus();document.addEventListener('keydown',f);return()=>{document.removeEventListener('keydown',f);old?.focus()}},[onClose]);return <div className="modal-bg" onMouseDown={e=>e.target===e.currentTarget&&onClose()}><section role="dialog" aria-modal="true" aria-label={title} className="dialog panel"><div className="panel-head"><h2>{title}</h2><button className="icon-btn" aria-label="Close" onClick={onClose}><X size={20}/></button></div>{children}</section></div>}
export function useLoad(path,refresh=0){
 const [result,set]=React.useState(null);
 React.useEffect(()=>{
  if(!path)return;const controller=new AbortController();
  api.get(path,{signal:controller.signal}).then(r=>set({path,refresh,data:r.data,error:''})).catch(e=>{if(!controller.signal.aborted)set({path,refresh,data:null,error:errorText(e)})});
  return()=>controller.abort();
 },[path,refresh]);
 return path&&result?.path===path&&result.refresh===refresh?result:{data:null,error:''};
}
export async function download(path){
 try{
  const {data,headers}=await api.get(path,{responseType:'blob'});
  const disposition=headers['content-disposition']||'';
  const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const fallback=disposition.match(/filename="([^"]+)"/i)?.[1];
  const name=fallback||(encoded?decodeURIComponent(encoded):`datapilot.${path.split('/').at(-1)}`);
  const url=URL.createObjectURL(new Blob([data],{type:'application/octet-stream'})),a=document.createElement('a');
  a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
 }catch(e){window.dispatchEvent(new CustomEvent('pilot-download-error',{detail:e.response?.status===404?'This file is no longer available.':errorText(e)}));}
}
export function ExportButtons({id}){return <><Btn icon={ArrowDownToLine} onClick={()=>download(`/datasets/${id}/export/csv`)}>CSV</Btn><Btn icon={ArrowDownToLine} onClick={()=>download(`/datasets/${id}/export/xlsx`)}>Excel</Btn></>}
export function LinkButton({children,onClick}){return <button className="text-btn" onClick={onClick}>{children}<ArrowRight size={15}/></button>}
