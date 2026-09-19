'use strict';
// Unit tests for tray/lib/recipes-panel.js -- FELIX-AUDIT S9 (F8).

const RecipesPanel = require('../lib/recipes-panel');
const ListSort = require('../lib/list-sort');

const R = (over) => Object.assign({ id: 'r1', name: 'Alpha', steps: [1, 2], run_count: 3, last_run_at: null }, over);
const opts = (mode) => ({ mode: mode, sort: ListSort, fmtDate: (d) => '<' + d + '>' });

describe('buildItems', () => {
  test('empty / missing payload gives no items', () => {
    expect(RecipesPanel.buildItems(null, opts('alpha'))).toEqual([]);
    expect(RecipesPanel.buildItems({ recipes: [] }, opts('alpha'))).toEqual([]);
  });

  test('shapes name, meta status, verify and Run/Delete tool actions', () => {
    const [item] = RecipesPanel.buildItems(
      { recipes: [R({ id: 'x9', verify: { ok: true }, last_run_at: '2026-01-02' })] }, opts('alpha'));
    expect(item.name).toBe('Alpha');
    expect(item.status).toBe('2 steps \xb7 run 3\xd7 \xb7 last <2026-01-02>');
    expect(item.verify).toEqual({ ok: true });
    expect(item.actions).toEqual([
      { id: 'recipe-run-x9', label: 'Run', tool: 'recipe_run', tool_args: { recipe_id: 'x9' } },
      { id: 'recipe-delete-x9', label: 'Delete', tool: 'recipe_delete', tool_args: { recipe_id: 'x9' } },
    ]);
  });

  test('singular step, no last run, verify defaults to null', () => {
    const [item] = RecipesPanel.buildItems({ recipes: [R({ steps: [1], run_count: 0 })] }, opts('alpha'));
    expect(item.status).toBe('1 step \xb7 run 0\xd7');
    expect(item.verify).toBeNull();
  });

  test('a stale id is flagged in the meta', () => {
    const items = RecipesPanel.buildItems(
      { recipes: [R({ id: 'a' }), R({ id: 'b', name: 'Beta' })], stale_ids: ['b'] }, opts('alpha'));
    expect(items[0].status).not.toMatch(/stale/);
    expect(items[1].status).toMatch(/stale$/);
  });
});

describe('sortRecipes', () => {
  const list = [
    R({ id: 'a', name: 'Bravo', run_count: 1, last_run_at: '2026-01-01T00:00:00Z' }),
    R({ id: 'b', name: 'alpha', run_count: 9, last_run_at: '2026-03-01T00:00:00Z' }),
    R({ id: 'c', name: 'Charlie', run_count: 5, last_run_at: '2026-02-01T00:00:00Z' }),
  ];
  const ids = (mode) => RecipesPanel.sortRecipes(list, mode, ListSort).map((x) => x.id);

  test('default is alphabetical, case-insensitive', () => expect(ids('alpha')).toEqual(['b', 'a', 'c']));
  test('run_count_desc', () => expect(ids('run_count_desc')).toEqual(['b', 'c', 'a']));
  test('last_run_desc', () => expect(ids('last_run_desc')).toEqual(['b', 'c', 'a']));
  test('does not mutate the input', () => {
    const copy = list.slice();
    RecipesPanel.sortRecipes(list, 'run_count_desc', ListSort);
    expect(list).toEqual(copy);
  });
});

describe('runResultText', () => {
  test('ok', () => expect(RecipesPanel.runResultText({ ok: true })).toBe('Done.'));
  test('error with message', () => expect(RecipesPanel.runResultText({ ok: false, message: 'boom' })).toBe('Error: boom'));
  test('error without message', () => expect(RecipesPanel.runResultText({})).toBe('Error: unknown'));
});
