const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('map resources resolve within the mounted path and preserve queries', () => {
  const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
  const declarations = source.slice(0, source.indexOf('const $ ='));
  for (const [href, prefix] of [
    ['http://127.0.0.1:8766/#national-G248', 'http://127.0.0.1:8766/'],
    ['https://ydl66.top/map/?view=104,30,9#national-G248', 'https://ydl66.top/map/'],
  ]) {
    const context = vm.createContext({ URL, location: { href } });
    vm.runInContext(declarations, context);
    for (const resource of ['/api/network?bbox=103,30,105,31&kinds=national', '/data/routes/national-G248.geojson', '/data/overview.geojson']) {
      context.resource = resource;
      assert.equal(vm.runInContext('resourceURL(resource)', context), prefix + resource.slice(1));
    }
    assert.equal(vm.runInContext('appBase.href', context), prefix);
  }
});
