// Opt-in browser check using the existing machine-wide Playwright installation.
const {chromium} = require('D:/npm-global/node_modules/playwright');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {spawn} = require('child_process');
const assert = require('assert/strict');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'anygpt-ui-'));
const project = path.resolve(__dirname, '..');
const run = path.join(root, 'captures', 'ui-run');
const codex = path.join(root, 'codex');
fs.mkdirSync(run, {recursive:true}); fs.mkdirSync(codex);
const index = path.join(codex, 'session_index.jsonl');
const record = (sample, id) => ({sample, thread_id:id, captured_at:new Date().toISOString(),
  request_model:'gpt-6-astra', response_model:'gpt-6-astra', requested_effort:'xhigh',
  response_effort:'high', effort_differs:true, reasoning_tokens:39, response_completed:true});
const addRecord = (row) => {
  fs.appendFileSync(path.join(run,'records.jsonl'), JSON.stringify(row)+'\n');
  fs.writeFileSync(path.join(run,`${String(row.sample).padStart(4,'0')}.json`),JSON.stringify(row));
};
const name = (id, thread_name) => fs.appendFileSync(index,JSON.stringify({id,thread_name})+'\n');
addRecord(record(1,'one')); name('one','查看今天天气');
const python = spawn(path.join(project,'.venv','Scripts','python.exe'),['-X','utf8','-u','-c',`
import sys
from pathlib import Path
from dashboard import DashboardServer
from conversation_names import ConversationNames
root=Path(sys.argv[1])
server=DashboardServer(('127.0.0.1',0),root/'captures',Path('web'))
server.store.names=ConversationNames(root/'codex')
server.controller.state='capturing'
server.controller.run='ui-run'
print(server.origin,flush=True)
server.serve_forever()
`,root],{cwd:project,windowsHide:true,stdio:['ignore','pipe','pipe']});
let browser, testOrigin;
(async () => {
  const origin = await new Promise((resolve,reject) => {
    python.stdout.once('data', data=>resolve(data.toString().trim()));
    python.once('error',reject); python.stderr.once('data',data=>reject(Error(data.toString())));
  });
  testOrigin = origin;
  browser = await chromium.launch({channel:'msedge',headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[]; page.on('pageerror', error=>errors.push(error.message));
  await page.goto(origin);
  await page.getByText('查看今天天气',{exact:true}).waitFor();
  assert.equal(await page.locator('#duration').inputValue(),'0');
  assert.match(await page.locator('#capture-info').textContent(),/所有对话/);
  name('two','定位 B0 首屏耗时'); addRecord(record(2,'two'));
  await page.getByText('定位 B0 首屏耗时',{exact:true}).waitFor({timeout:12000});
  name('one','重命名后的对话');
  await page.getByText('重命名后的对话',{exact:true}).waitFor({timeout:12000});
  await page.locator('#search').fill('重命名后的对话');
  assert.equal(await page.locator('#rows tr').count(),1);
  await page.locator('#rows button').click();
  await page.locator('#detail-content').getByText('重命名后的对话',{exact:true}).waitFor();
  await page.locator('#close-detail').click();
  await page.locator('#search').fill('');
  for (const width of [320,768,1024,1440]) {
    await page.setViewportSize({width,height:1000});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
  }
  assert.deepEqual(errors,[]);
  const actual = JSON.parse(fs.readFileSync(path.join(project,'ui-state.json'),'utf8'));
  await page.goto(actual.url);
  await page.locator('#rows tr').first().waitFor({timeout:10000});
  console.log(JSON.stringify({checks:'new conversation, rename without reload, search, detail, continuous default, 4 viewport widths',
    actual_names:await page.locator('.conversation-cell span').allTextContents(),browser_errors:errors}));
  await page.screenshot({path:path.join(project,'preview.png'),fullPage:true});
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  if(testOrigin) {
    const html = await (await fetch(testOrigin)).text();
    const token = html.split('name="anygpt-token" content="')[1].split('"')[0];
    await fetch(testOrigin+'/api/quit',{method:'POST',headers:{'X-AnyGPT-Token':token},body:'{}'});
  } else python.kill();
  await new Promise(resolve=>python.exitCode!==null?resolve():python.once('exit',resolve));
  // Only the temp directory returned by mkdtempSync above is removed.
  if(path.dirname(root)!==path.resolve(os.tmpdir())) throw Error('Unexpected temporary path');
  fs.rmSync(root,{recursive:true,force:true});
});
