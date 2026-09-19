/* Recipes panel helpers -- FELIX-AUDIT S9 (F8): the first panel extracted out of
 * tray/windows/main.html's inline script.
 * Dual-mode: window.RecipesPanel in the renderer; module.exports for Node tests.
 * IIFE prevents top-level const collisions when loaded via <script src>.
 *
 * Pure data shapers -- no DOM, no IPC. The renderer turns buildItems() into the
 * shared registry widget; tests assert structure/content directly.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.RecipesPanel = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* Sort recipes by the Library-pane sort mode. `sort` is the shared list-sort
   * helper ({ sortBy, dateKey, alphaKey }), injected so this stays DOM-free. */
  function sortRecipes(recipes, mode, sort) {
    if (mode === 'last_run_desc') {
      return sort.sortBy(recipes, function (x) { return sort.dateKey(x.last_run_at); }, 'desc');
    }
    if (mode === 'run_count_desc') {
      return sort.sortBy(recipes, function (x) { return x.run_count || 0; }, 'desc');
    }
    return sort.sortBy(recipes, function (x) { return sort.alphaKey(x.name); }, 'asc');
  }

  /* recipesData is the recipes_update payload: { recipes: [], stale_ids: [] }.
   * opts: { mode, sort, fmtDate }. Returns the registry-widget items: name /
   * step+run meta / VerifyResult badge / Run+Delete as declared
   * recipe_run / recipe_delete tool calls (ADR-0035 J). */
  function buildItems(recipesData, opts) {
    var recipes = (recipesData && recipesData.recipes) || [];
    var staleIds = {};
    ((recipesData && recipesData.stale_ids) || []).forEach(function (id) { staleIds[id] = true; });
    var fmtDate = opts.fmtDate || String;

    return sortRecipes(recipes, opts.mode, opts.sort).map(function (rcp) {
      var stepCount = Array.isArray(rcp.steps) ? rcp.steps.length : 0;
      var metaParts = [
        stepCount + ' step' + (stepCount === 1 ? '' : 's'),
        'run ' + (rcp.run_count || 0) + '\xd7',
      ];
      if (rcp.last_run_at) metaParts.push('last ' + fmtDate(rcp.last_run_at));
      if (staleIds[rcp.id]) metaParts.push('stale');
      return {
        name: rcp.name,
        status: metaParts.join(' \xb7 '),
        verify: rcp.verify || null,
        actions: [
          { id: 'recipe-run-' + rcp.id, label: 'Run', tool: 'recipe_run', tool_args: { recipe_id: rcp.id } },
          { id: 'recipe-delete-' + rcp.id, label: 'Delete', tool: 'recipe_delete', tool_args: { recipe_id: rcp.id } },
        ],
      };
    });
  }

  /* Status text for a recipe_run_result event. */
  function runResultText(data) {
    return data && data.ok ? 'Done.' : ('Error: ' + ((data && data.message) || 'unknown'));
  }

  return { sortRecipes: sortRecipes, buildItems: buildItems, runResultText: runResultText };
}));
