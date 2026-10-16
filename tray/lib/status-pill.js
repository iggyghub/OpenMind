(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else if (typeof window !== 'undefined') {
    root.StatusPillMod = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function pillView(baseState, working, stateLabels, humanize) {
    const count = (working && typeof working.count === 'number') ? working.count : 0;
    if (baseState === 'thinking' || baseState === 'speaking' || !(count > 0)) {
      return { state: baseState, label: stateLabels[baseState] || baseState };
    }
    return { state: 'working', label: 'Working: ' + humanize(working.tool) + (count > 1 ? ' (+' + (count - 1) + ')' : '') };
  }
  return { pillView };
});
