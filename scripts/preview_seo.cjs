/* Loopback-only preview: real candidate handler + filesystem origin + future 404.
   node scripts/preview_seo.cjs --port 8767
   /__seo_www__/path simulates the www viewer host. Location is rewritten to
   loopback; X-Preview-CloudFront-Location retains the candidate HTTPS location.
   This is a simulation, not AWS staging or a complete CloudFront emulator. */
'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, 'infra/site/functions/unified-viewer-request.js'), 'utf8'), context);
const portIndex = process.argv.indexOf('--port');
const port = portIndex === -1 ? 0 : Number(process.argv[portIndex + 1]);
const types = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8',
    '.json':'application/json','.xml':'application/xml','.txt':'text/plain; charset=utf-8','.pdf':'application/pdf',
    '.png':'image/png','.webp':'image/webp','.jpg':'image/jpeg','.jpeg':'image/jpeg','.svg':'image/svg+xml',
    '.ico':'image/x-icon','.woff2':'font/woff2','.woff':'font/woff'};
const excluded = new Set(['infra','scripts','.git','.github','.claude','docs','_dist','_nucleo-wordpress','vms_web_backup','assets - copia']);
const server = http.createServer((req,res) => {
    res.setHeader('Cache-Control','no-store');
    res.setHeader('X-Preview-Mode','candidate-function-and-future-404-simulation');
    // Prevent analytics, forms, fonts or other remote services from being called.
    res.setHeader('Content-Security-Policy',"default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-src 'none'; form-action 'none'");
    if(!['GET','HEAD','OPTIONS'].includes(req.method)){res.writeHead(405);res.end();return;}
    const question=req.url.indexOf('?');
    let uri=question===-1?req.url:req.url.slice(0,question);
    const search=question===-1?'':req.url.slice(question+1);
    const host=uri.startsWith('/__seo_www__/')?'www.vmsenergy.com':'vmsenergy.com';
    if(host==='www.vmsenergy.com') uri=uri.slice('/__seo_www__'.length);
    const query=Object.create(null);
    // Preserve encoded event strings: URLSearchParams would decode escapes/+.
    for(const item of search?search.split('&'):[]){
        const i=item.indexOf('=');const key=i<0?item:item.slice(0,i);const value=i<0?'':item.slice(i+1);
        if(Object.hasOwn(query,key)){
            if(!query[key].multiValue)query[key].multiValue=[{value:query[key].value}];
            query[key].multiValue.push({value});
        }else query[key]={value};
    }
    const output=context.handler({version:'1.0',context:{eventType:'viewer-request'},request:{uri,method:req.method,headers:{host:{value:host}},cookies:{},querystring:query}});
    if(output.statusCode){
        const production=output.headers.location.value;
        const location=production.slice('https://vmsenergy.com'.length);
        res.setHeader('X-Preview-CloudFront-Location',production);
        res.writeHead(output.statusCode,{Location:'http://127.0.0.1:'+server.address().port+location});res.end();return;
    }
    if(req.method==='OPTIONS'){res.writeHead(204,{Allow:'GET, HEAD, OPTIONS'});res.end();return;}
    res.setHeader('X-Preview-Origin-URI',output.uri);
    let file;
    try {
        const decoded=decodeURIComponent(output.uri);
        const segments=decoded.split('/').filter(Boolean);
        file=path.resolve(root,'.'+(output.uri==='/'?'/index.html':decoded));
        if(segments.some(s=>s.startsWith('.'))||excluded.has(segments[0])||file!==root&&!file.startsWith(root+path.sep))file=null;
    }catch(_){res.writeHead(400);res.end('Invalid URI encoding');return;}
    const exists=file&&fs.existsSync(file)&&fs.statSync(file).isFile();
    if(!exists)file=path.join(root,'404.html');
    const content=fs.readFileSync(file);
    res.writeHead(exists?200:404,{'Content-Type':types[path.extname(file).toLowerCase()]||'application/octet-stream','Content-Length':content.length});
    res.end(req.method==='HEAD'?undefined:content);
});
server.listen(port,'127.0.0.1',()=>console.log(JSON.stringify({url:'http://127.0.0.1:'+server.address().port,mode:'CloudFront candidate + future 404 simulation; external services blocked'})));
