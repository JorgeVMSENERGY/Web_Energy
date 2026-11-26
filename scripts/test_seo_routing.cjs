/* Offline CloudFront event tests; never contacts AWS. Node built-ins only. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const code = fs.readFileSync(path.join(root, 'infra/site/functions/unified-viewer-request.js'), 'utf8');
const liveBytes = fs.readFileSync(path.join(__dirname, 'seo/fixtures/english-live-20261125.js'));
const rules = JSON.parse(fs.readFileSync(path.join(root, 'infra/site/functions/legacy-redirects.json'), 'utf8')).redirects;
const candidate = vm.createContext({});
const live = vm.createContext({});
vm.runInContext(code, candidate);
vm.runInContext(liveBytes.toString('utf8'), live);
const clone = value => JSON.parse(JSON.stringify(value));
const plain = value => clone(value);
const results = [], comparisons = [];

function event(uri, host = 'vmsenergy.com', method = 'GET', querystring = {}) {
    return { version: '1.0', context: { eventType: 'viewer-request', distributionId: 'E3FWVCRXQ69DI6' },
        viewer: { ip: '192.0.2.1' }, request: { method, uri, querystring,
            headers: { host: { value: host }, accept: { value: '*/*' }, 'x-test': { value: 'unchanged' } },
            cookies: { session: { value: 'opaque' } } } };
}
function test(name, category, action) {
    try { action(); results.push({name, category, passed: true}); }
    catch (error) { results.push({name, category, passed: false, error: error.stack}); }
}
function compare(input) {
    const previous = plain(live.handler(clone(input)));
    const next = plain(candidate.handler(clone(input)));
    const request = input.request;
    const host = request.headers.host ? request.headers.host.value.toLowerCase().replace(/\.$/, '') : '';
    const eligible = ['GET', 'HEAD'].includes(request.method);
    const expectedChange = eligible && (Object.hasOwn(rules, request.uri.replace(/\/+$/, '')) || host === 'www.vmsenergy.com');
    const different = JSON.stringify(previous) !== JSON.stringify(next);
    const classification = different ? (expectedChange ? 'AUTHORIZED_CHANGE' : 'UNEXPECTED_REGRESSION') : 'UNCHANGED';
    comparisons.push({ uri: request.uri, host, method: request.method, querystring: request.querystring,
        classification, live: previous, candidate: next });
    assert.notEqual(classification, 'UNEXPECTED_REGRESSION');
    return next;
}
function location(input) {
    const response = candidate.handler(input);
    assert.equal(response.statusCode, 301);
    assert.equal(response.statusDescription, 'Moved Permanently');
    assert.deepEqual(Object.keys(response.headers), ['location']);
    return response.headers.location.value;
}

for (const [source, target] of Object.entries(rules)) {
    for (const method of ['GET', 'HEAD']) for (const host of ['vmsenergy.com', 'www.vmsenergy.com']) for (const slash of ['', '/']) {
        test(`${method} ${host}${source}${slash}`, 'matrix', () => {
            const input = event(source + slash, host, method);
            const output = compare(input);
            assert.equal(output.statusCode, 301);
            assert.equal(output.headers.location.value, 'https://vmsenergy.com' + target);
            const follow = candidate.handler(event(target));
            assert.equal(follow.statusCode, undefined, 'No redirect chain or loop at the final target');
            assert.equal(follow.uri, target);
        });
    }
    test('multiple trailing slashes ' + source, 'matrix-extra', () => {
        assert.equal(location(event(source + '///')), 'https://vmsenergy.com' + target);
    });
}

const queryCases = [
    ['campaigns', {utm_source:{value:'google'},utm_medium:{value:'cpc'},utm_campaign:{value:'media%20tension'},utm_term:{value:'celdas'},utm_content:{value:'hero'},gclid:{value:'g1'},fbclid:{value:'f1'},msclkid:{value:'m1'}}, 'utm_source=google&utm_medium=cpc&utm_campaign=media%20tension&utm_term=celdas&utm_content=hero&gclid=g1&fbclid=f1&msclkid=m1'],
    ['repeated', {tag:{value:'one',multiValue:[{value:'one'},{value:'two'},{value:''}]}}, 'tag=one&tag=two&tag='],
    ['flag and empty', {flag:{value:''},empty:{value:''}}, 'flag=&empty='],
    ['escapes', {q:{value:'%20%26%2B%25%3F%3D%23'}}, 'q=%20%26%2B%25%3F%3D%23'],
    ['encoded UTF8', {q:{value:'tensi%C3%B3n%20%E2%9A%A1'}}, 'q=tensi%C3%B3n%20%E2%9A%A1'],
    ['raw UTF8', {'tensión':{value:'café ⚡ 😀'}}, 'tensi%C3%B3n=caf%C3%A9%20%E2%9A%A1%20%F0%9F%98%80'],
    ['raw delimiters', {q:{value:'a?b=c#d&e'}}, 'q=a%3Fb%3Dc%23d%26e'],
    ['encoded keys', {'utm%5Fsource':{value:'a+b%2Bc'}}, 'utm%5Fsource=a+b%2Bc'],
    ['invalid literal percent', {q:{value:'100% %zz %2520'}}, 'q=100%25%20%25zz%20%2520'],
    ['header control chars', {q:{value:'a\r\nLocation: https://evil.example/#x'}}, 'q=a%0D%0ALocation%3A%20https%3A%2F%2Fevil.example%2F%23x'],
    ['empty key', {'':{value:'value'}}, '=value'],
    ['multiValue supersedes value', {id:{value:'ignored',multiValue:[{value:'actual'},{value:'second'}]}}, 'id=actual&id=second']
];
for (const [name, query, expected] of queryCases) {
    for (const method of ['GET','HEAD']) test(name + ' ' + method, 'query', () => {
        const input = event('/nosotros/', 'www.vmsenergy.com', method, query);
        const before = clone(input);
        assert.equal(location(input), 'https://vmsenergy.com/nosotros.html?' + expected);
        assert.deepEqual(input, before);
        compare(input);
    });
}
test('future static fragment receives query first', 'query', () => {
    assert.equal(candidate.redirectLocation('/sostenibilidad.html#informe', {utm_source:{value:'test'}}),
        'https://vmsenergy.com/sostenibilidad.html?utm_source=test#informe');
});

for (const host of ['www.vmsenergy.com','WWW.VMSENERGY.COM','www.vmsenergy.com.']) {
    for (const uri of ['/','/en','/en/','/index.html','/unknown/deep.html','/CaseSensitive.PDF','//evil.example/path','/a%20b/%E2%9A%A1']) {
        test('www ' + host + uri, 'www', () => {
            const input = event(uri, host, 'GET', {gclid:{value:'a%2Bb'}});
            const result = compare(input);
            assert.equal(result.statusCode, 301);
            assert.equal(result.headers.location.value, 'https://vmsenergy.com' + uri + '?gclid=a%2Bb');
            assert.equal(new URL(result.headers.location.value).hostname, 'vmsenergy.com');
        });
    }
}
for (const host of ['vmsenergy.com','www.example.com','www.vmsenergy.com.evil.example','d34jb94w5ivknd.cloudfront.net','vmsenergy.com:443']) {
    for (const uri of ['/','/index.html','/Nosotros','/NOSOTROS/','/constructor','/__proto__','/toString','/en','/en/','/en//','/en/desconocida','/en/nosotros','/en/about-us','/en/nosotros.html','/en/contacto.html','/en/lp-cables-especiales.html','/en/soluciones_oil_gas.html','/en/testing-commissioning.html','/assets/img/Logo.PNG','/nested/unknown.html','/a%26b','/niño/%E2%9A%A1','/index.html///']) {
        for (const method of ['GET','HEAD','OPTIONS']) test('preserve ' + method + ' ' + host + uri, 'regression', () => {
            const input = event(uri, host, method, {x:{value:'1',multiValue:[{value:'1'},{value:'2'}]}});
            const before = clone(input);
            const result = compare(input);
            assert.equal(result.statusCode, undefined);
            assert.equal(result.uri, uri === '/en' || uri === '/en/' ? '/en/index.html' : uri);
            const after = clone(before.request);after.uri = result.uri;
            assert.deepEqual(result, after);
            const originalRequest = input.request;
            assert.equal(candidate.handler(input), originalRequest, 'Pass through the original request object');
        });
    }
}
const historical = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, 'infra/site/functions/redirects.js'), 'utf8'), historical);
const dormant = Object.keys(historical.REDIRECTS).filter(key => !Object.hasOwn(rules, key));
for (const uri of [...dormant, ...Object.keys(historical.EN_REDIRECTS)]) {
    test('dormant ' + uri, 'dormant', () => {
        const output = compare(event(uri));
        assert.equal(output.uri, uri);
        assert.equal(output.statusCode, undefined);
    });
}
for (const uri of ['/en','/en/','/nosotros','/nosotros/','/assets/x.css']) {
    test('OPTIONS www ' + uri, 'methods', () => {
        const output = compare(event(uri, 'www.vmsenergy.com', 'OPTIONS'));
        assert.equal(output.statusCode, undefined);
    });
}
test('inherited redirect keys are ignored', 'security', () => {
    vm.runInContext("Object.prototype['/inherited']='/nosotros.html'", candidate);
    try { assert.equal(candidate.handler(event('/inherited')).uri, '/inherited'); }
    finally { vm.runInContext("delete Object.prototype['/inherited']", candidate); }
});
test('inherited query keys are ignored', 'security', () => {
    const query = Object.create({inherited:{value:'malicious'}});
    query.own = {value:'good'};
    assert.equal(location(event('/nosotros','vmsenergy.com','GET',query)), 'https://vmsenergy.com/nosotros.html?own=good');
});
test('own special query names survive', 'security', () => {
    const query = JSON.parse('{"__proto__":{"value":"safe"},"constructor":{"value":"also-safe"}}');
    assert.equal(location(event('/nosotros','vmsenergy.com','GET',query)), 'https://vmsenergy.com/nosotros.html?__proto__=safe&constructor=also-safe');
});
for (const uri of ['/\\evil.example','/x\r\nLocation: https://evil.example','/x#fragment','/x?injected=true']) {
    test('safe authority and header ' + JSON.stringify(uri), 'security', () => {
        const url = location(event(uri,'www.vmsenergy.com'));
        assert.equal(new URL(url).hostname, 'vmsenergy.com');
        assert.equal(new URL(url).hash, '');
        assert.equal(new URL(url).search, '');
        assert(!/[\r\n]/.test(url));
    });
}
test('missing host preserves baseline', 'security', () => {
    const input = event('/en/');delete input.request.headers.host;
    assert.equal(compare(input).uri, '/en/index.html');
});
// Phase 3: independent URL parsing checks semantic values, not serializer text.
// Large cases stay below the documented incoming URL limit; Node cannot certify
// CloudFront compute utilization or acceptance of an over-limit viewer request.
for (const method of ['GET', 'HEAD']) {
    test('long query (7500 ASCII bytes) ' + method, 'edge', () => {
        const value = 'x'.repeat(7500);
        const result = location(event('/nosotros', 'www.vmsenergy.com', method, {q:{value}}));
        const url = new URL(result);
        assert.equal(url.searchParams.get('q'), value);
        assert.equal(url.hostname, 'vmsenergy.com');
        assert(Buffer.byteLength(result) < 8192);
    });
    for (const count of [10, 100, 500]) test('many repeated parameters ' + count + ' ' + method, 'edge', () => {
        const values = Array.from({length:count}, (_,i)=>({value:i%3===0?'':String(i)}));
        const query = {id:{value:values[0].value,multiValue:values}};
        const result = location(event('/nosotros', 'www.vmsenergy.com', method, query));
        assert.deepEqual(new URL(result).searchParams.getAll('id'), values.map(x=>x.value));
        assert(Buffer.byteLength(result)<8192);
    });
    test('semantic Unicode and special parameter names ' + method, 'edge', () => {
        const query = Object.create(null);
        const entries = [
            ['__proto__', 'safe'], ['constructor', '%3F%3D%23%26%2B%25'],
            ['toString',''], ['hasOwnProperty','%FF'], ['__defineGetter__','%2526'],
            ['0','zero'], ['1','one'], ['', 'empty-name'], ['a%26b','%00'],
            ['café','tensión ⚡ 😀'], ['e\u0301','A+B%2BC'], ['a+b','x%20y']
        ];
        for(const [key,value] of entries)query[key]={value};
        const result = new URL(location(event('/nosotros', 'www.vmsenergy.com', method, query)));
        // URLSearchParams parses the expected raw event spelling separately.
        // Encoded invalid UTF-8 is preserved at the byte level, decoded by URL
        // parsing as replacement characters; it is never decoded by the handler.
        for(const [key,value] of entries){
            const expected = new URLSearchParams(key+'='+value);
            assert.deepEqual(result.searchParams.getAll([...expected.keys()][0]), [...expected.values()]);
        }
        assert.equal(result.searchParams.size,entries.length);
        assert.equal(result.hostname,'vmsenergy.com');
    });
}
const pathCorpus = [
    '/nosotros%2f', '/nos%6Ftros/', '/en%2F', '/%2e%2e/path', '/a%2Fb/%5C',
    '/a%20b/%26%2B%25%3F%3D%23', '/%ff/%c3%b1', '/café/niño/😀',
    '/e\u0301/%E2%9A%A1', '/100%/%zz/%252F', '//evil.example/path',
    '/\\evil.example/a', '/a\\b//', '/a#b?c=d', '/a%0d%0aLocation%3aevil'
];
for(const uri of pathCorpus){
    test('safePath idempotence ' + JSON.stringify(uri), 'edge', () => {
        const once = candidate.safePath(uri);
        assert.equal(candidate.safePath(once),once);
        assert(!/[\r\n\\?#]/.test(once));
        const result = location(event(uri,'www.vmsenergy.com','GET',{q:{value:'%26%2B%25'}}));
        assert.equal(result,'https://vmsenergy.com'+once+'?q=%26%2B%25');
        assert.equal(new URL(result).hostname,'vmsenergy.com');
    });
    for(const host of ['vmsenergy.com','www.vmsenergy.com','www.example.com','d34jb94w5ivknd.cloudfront.net']) {
        for(const method of ['GET','HEAD','OPTIONS'])test('encoded path contract '+host+' '+method+' '+JSON.stringify(uri), 'edge', () => {
            const input=event(uri,host,method,{tag:{value:'',multiValue:[{value:''},{value:'%2B'}]}});
            const output=compare(input);
            if(host==='www.vmsenergy.com'&&method!=='OPTIONS'){
                assert.equal(output.statusCode,301);
                assert.equal(new URL(output.headers.location.value).hostname,'vmsenergy.com');
                assert.deepEqual(new URL(output.headers.location.value).searchParams.getAll('tag'),['','+']);
            }else{
                assert.equal(output.statusCode,undefined);
                assert.deepEqual(output,input.request);
            }
        });
    }
}
test('grouped repeated keys preserve values without promising interleaved order', 'edge', () => {
    const result=new URL(location(event('/nosotros','vmsenergy.com','GET',{
        a:{value:'1',multiValue:[{value:'1'},{value:'3'}]},b:{value:'2'}
    })));
    assert.deepEqual(result.searchParams.getAll('a'),['1','3']);
    assert.deepEqual(result.searchParams.getAll('b'),['2']);
    assert.equal(result.search,'?a=1&a=3&b=2');
});
test('fixed LIVE fixture checksum', 'runtime', () => {
    assert.equal(crypto.createHash('sha256').update(liveBytes).digest('hex'), '3fbd770c83a207606e516c0f4667a9a7f3893b088d0e4046005d1489f92e2d1c');
});
test('approved audit matrix is immutable', 'runtime', () => {
    // These ASCII paths have the same lexical order in Python and JavaScript.
    const sorted = JSON.stringify(Object.fromEntries(Object.keys(rules).sort().map(key=>[key,rules[key]])));
    assert.equal(crypto.createHash('sha256').update(sorted).digest('hex'), '9c64ae7b2bad550dfa6d19a963cdfbb7f2db14a73db9cb517cfebeec7dfa2e71');
    assert.deepEqual(plain(candidate.REDIRECTS), rules);
});
test('runtime restricted subset and size', 'runtime', () => {
    assert(Buffer.byteLength(code) <= 10240);
    assert(!/\b(require|eval|fetch|setTimeout|process|Buffer|async|await)\s*\(/.test(code));
    new vm.Script(code);
    assert.equal(Object.keys(candidate.REDIRECTS).length, 24);
});
const report = {passed: results.filter(r=>r.passed).length, failed: results.filter(r=>!r.passed).length,
    skipped:0, matrixCases: results.filter(r=>r.category==='matrix').length,
    matrixPassed:results.filter(r=>r.category==='matrix'&&r.passed).length,
    unexpectedRegressions:comparisons.filter(r=>r.classification==='UNEXPECTED_REGRESSION').length,
    functionSizeBytes:Buffer.byteLength(code), dormantEsCount:dormant.length,
    results, comparisons};
const reportIndex=process.argv.indexOf('--report');
if(reportIndex!==-1) fs.writeFileSync(process.argv[reportIndex+1],JSON.stringify(report,null,2));
for(const result of results.filter(r=>!r.passed)) console.error(result.name+'\n'+result.error);
console.log(JSON.stringify({passed:report.passed,failed:report.failed,matrixCases:report.matrixCases,
    matrixPassed:report.matrixPassed,unexpectedRegressions:report.unexpectedRegressions,functionSizeBytes:report.functionSizeBytes,dormantEsCount:dormant.length}));
process.exitCode = report.failed ? 1 : 0;
