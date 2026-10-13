'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {minimumZoom, bookmarks} = require('../web/view-controls.js');

test('300 km scale limit follows latitude across China', () => {
  const radius = 6371008.8;
  for (const latitude of [3, 18, 30.66, 35, 45, 55]) {
    const zoom = minimumZoom(latitude);
    const longitudeSpan = 2 * Math.PI * 100 / (512 * 2 ** zoom);
    const distance = 2 * radius * Math.asin(Math.cos(latitude * Math.PI / 180) * Math.sin(longitudeSpan / 2));
    // MapLibre rounds a scale width between 300 and 500 km down to 300 km.
    assert.ok(distance >= 399000 && distance <= 400000, `${latitude}: ${distance}`);
  }
  assert.ok(minimumZoom(18) > minimumZoom(55));
});

test('restore returns the complete view before the first overview', () => {
  let camera = {center: {lng: 104.065, lat: 30.66}, zoom: 8, bearing: 12, pitch: 25};
  const map = {
    getCenter: () => camera.center,
    getZoom: () => camera.zoom,
    getBearing: () => camera.bearing,
    getPitch: () => camera.pitch,
  };
  const views = bookmarks();
  assert.equal(views.has(), false);
  views.save(map);
  camera.center.lng = 115;
  camera = {center: {lng: 110, lat: 36}, zoom: 5, bearing: 0, pitch: 0};
  views.save(map);
  assert.deepEqual(views.take(), {center: [104.065, 30.66], zoom: 8, bearing: 12, pitch: 25});
  assert.equal(views.has(), false);
  assert.equal(views.take(), null);
  views.save(map);
  assert.deepEqual(views.take(), {center: [110, 36], zoom: 5, bearing: 0, pitch: 0});
});
