/**
 * MechtaBot DB — Google Таблица как база данных бота.
 *
 * Код живет в Apps Script, привязанном к таблице (Расширения → Apps Script),
 * и развернут как веб-приложение (Deploy → Web app, Execute as: Me,
 * Who has access: Anyone). Бот ходит сюда из services/db_service.py:
 * POST <URL веб-приложения> с JSON {secret, action, ...}.
 *
 * Перед первым деплоем один раз запустить setup(): создаст листы с заголовками
 * и сгенерирует секрет (Script Properties → DB_SECRET), который нужно положить
 * в переменную окружения бота GSHEETS_DB_SECRET.
 */

var SCHEMA = {
  stand_builds: ['stand_key', 'stand_label', 'commit_message', 'actor', 'build_date', 'url', 'created_at'],
  monthly_report_state: ['id', 'last_sent_month'],
  autotest_bug_subtasks: ['subtask_key', 'parent_key', 'created_at'],
  autotest_runs: ['run_id', 'run_number', 'status', 'conclusion', 'branch', 'actor', 'commit_message', 'url',
                  'run_date', 'tests', 'passing', 'failing', 'pending', 'skipped', 'created_at'],
  autotest_run_specs: ['run_id', 'spec_name', 'duration', 'tests', 'passing', 'failing', 'pending', 'skipped'],
  aplaut_notified_reviews: ['review_id', 'created_at']
};

function setup() {
  var ss = SpreadsheetApp.getActive();
  Object.keys(SCHEMA).forEach(function (name) {
    var sh = ss.getSheetByName(name) || ss.insertSheet(name);
    var headers = SCHEMA[name];
    sh.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
    sh.setFrozenRows(1);
    // Все как текст: иначе Таблицы превращают даты/длинные id в числа и даты
    sh.getRange(1, 1, sh.getMaxRows(), headers.length).setNumberFormat('@');
  });
  var def = ss.getSheetByName('Sheet1') || ss.getSheetByName('Лист1');
  if (def && ss.getSheets().length > 1 && def.getLastRow() === 0) ss.deleteSheet(def);

  var props = PropertiesService.getScriptProperties();
  if (!props.getProperty('DB_SECRET')) {
    props.setProperty('DB_SECRET', Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, ''));
  }
  Logger.log('Готово. Секрет лежит в Project Settings → Script Properties → DB_SECRET');
}

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);
    if (req.secret !== PropertiesService.getScriptProperties().getProperty('DB_SECRET')) {
      return json_({ ok: false, error: 'unauthorized' });
    }
    var lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      var ops = req.action === 'batch' ? req.ops : [req];
      var results = ops.map(run_);
      return json_({ ok: true, result: req.action === 'batch' ? results : results[0] });
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function run_(op) {
  switch (op.action) {
    case 'ping': return 'pong';
    case 'select': return select_(op);
    case 'append': return append_(op.sheet, op.rows);
    case 'upsert': return upsert_(op.sheet, op.key, op.rows);
    case 'insert_if_absent': return insertIfAbsent_(op.sheet, op.key, op.rows);
    case 'delete': return delete_(op.sheet, op.where);
    case 'count': return read_(op.sheet).rows.length;
    default: throw new Error('unknown action: ' + op.action);
  }
}

function sheet_(name) {
  if (!SCHEMA[name]) throw new Error('unknown sheet: ' + name);
  var sh = SpreadsheetApp.getActive().getSheetByName(name);
  if (!sh) throw new Error('sheet missing, run setup(): ' + name);
  return sh;
}

function read_(name) {
  var sh = sheet_(name);
  var headers = SCHEMA[name];
  var last = sh.getLastRow();
  var values = last > 1 ? sh.getRange(2, 1, last - 1, headers.length).getValues() : [];
  var rows = values.map(function (v, i) {
    var o = { _row: i + 2 };
    headers.forEach(function (h, j) { o[h] = v[j] === '' ? null : String(v[j]); });
    return o;
  });
  return { sh: sh, headers: headers, rows: rows };
}

function matches_(row, where) {
  return Object.keys(where || {}).every(function (k) {
    var want = where[k];
    if (Array.isArray(want)) return want.map(String).indexOf(row[k]) !== -1;
    return row[k] === String(want);
  });
}

function select_(op) {
  var rows = read_(op.sheet).rows.filter(function (r) { return matches_(r, op.where); });
  if (op.order_by) {
    var col = op.order_by, dir = op.desc ? -1 : 1;
    rows.sort(function (a, b) {
      // stable: при равенстве более поздняя строка таблицы считается "больше"
      var x = a[col] || '', y = b[col] || '';
      return (x < y ? -1 : x > y ? 1 : a._row - b._row) * dir;
    });
  }
  if (op.limit) rows = rows.slice(0, op.limit);
  rows.forEach(function (r) { delete r._row; });
  return rows;
}

function toRow_(headers, obj) {
  return headers.map(function (h) {
    var v = obj[h];
    if (v === undefined || v === null) v = h === 'created_at' ? new Date().toISOString() : '';
    v = String(v);
    // Не даем строкам вида "=..." стать формулами
    return /^[=+\-@]/.test(v) ? "'" + v : v;
  });
}

function append_(name, objs) {
  if (!objs || !objs.length) return 0;
  var sh = sheet_(name), headers = SCHEMA[name];
  var values = objs.map(function (o) { return toRow_(headers, o); });
  sh.getRange(sh.getLastRow() + 1, 1, values.length, headers.length).setNumberFormat('@').setValues(values);
  return values.length;
}

function keyOf_(obj, key) {
  return [].concat(key).map(function (k) { return String(obj[k]); }).join('\u0001');
}

function upsert_(name, key, objs) {
  var data = read_(name);
  var index = {};
  data.rows.forEach(function (r) { index[keyOf_(r, key)] = r; });
  var toAppend = [];
  objs.forEach(function (o) {
    var existing = index[keyOf_(o, key)];
    if (!existing) { toAppend.push(o); return; }
    var merged = {};
    data.headers.forEach(function (h) { merged[h] = o[h] !== undefined ? o[h] : existing[h]; });
    data.sh.getRange(existing._row, 1, 1, data.headers.length).setValues([toRow_(data.headers, merged)]);
  });
  append_(name, toAppend);
  return objs.length;
}

function insertIfAbsent_(name, key, objs) {
  var seen = {};
  read_(name).rows.forEach(function (r) { seen[keyOf_(r, key)] = true; });
  var inserted = [], toAppend = [];
  objs.forEach(function (o) {
    var k = keyOf_(o, key);
    if (seen[k]) { inserted.push(false); return; }
    seen[k] = true;
    toAppend.push(o);
    inserted.push(true);
  });
  append_(name, toAppend);
  return inserted;
}

function delete_(name, where) {
  var data = read_(name);
  var doomed = data.rows.filter(function (r) { return matches_(r, where); });
  for (var i = doomed.length - 1; i >= 0; i--) data.sh.deleteRow(doomed[i]._row);
  return doomed.length;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
