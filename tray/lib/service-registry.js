/* Service registry for the S17 (#300) service directory.
 * Dual-mode: window.ServiceRegistry in the renderer; module.exports for Node tests.
 * IIFE prevents top-level const collisions when loaded via <script src>.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.ServiceRegistry = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* credAnchor: ID of the credentials card to scroll to on "Connect".
   * null  = local tool, no credentials needed.
   * 'cred-card-google'    = needs Google OAuth.
   * 'cred-card-api-keys'  = needs an API key in the static-token store. */
  var SERVICES = [
    // Google
    { name: 'Gmail',               category: 'Google',        credAnchor: 'cred-card-google' },
    { name: 'Google Calendar',     category: 'Google',        credAnchor: 'cred-card-google' },
    { name: 'Google Drive',        category: 'Google',        credAnchor: 'cred-card-google' },
    { name: 'Google Docs',         category: 'Google',        credAnchor: 'cred-card-google' },
    { name: 'Google Sheets',       category: 'Google',        credAnchor: 'cred-card-google' },
    { name: 'Google Slides',       category: 'Google',        credAnchor: 'cred-card-google' },
    { name: 'Google Contacts',     category: 'Google',        credAnchor: 'cred-card-google' },
    { name: 'Google Maps',         category: 'Google',        credAnchor: 'cred-card-google' },
    { name: 'Google Tasks',        category: 'Google',        credAnchor: 'cred-card-google' },
    // Dev
    { name: 'Git',                 category: 'Dev',           credAnchor: null },
    { name: 'GitHub / GitLab',     category: 'Dev',           credAnchor: 'cred-card-api-keys' },
    { name: 'Docker',              category: 'Dev',           credAnchor: null },
    { name: 'Package Managers',    category: 'Dev',           credAnchor: null },
    { name: 'SSH',                 category: 'Dev',           credAnchor: null },
    { name: 'HTTP Client',         category: 'Dev',           credAnchor: null },
    // Information
    { name: 'Wikipedia',           category: 'Information',   credAnchor: null },
    { name: 'Weather',             category: 'Information',   credAnchor: null },
    { name: 'News',                category: 'Information',   credAnchor: 'cred-card-api-keys' },
    { name: 'Stocks / Crypto',     category: 'Information',   credAnchor: 'cred-card-api-keys' },
    // Security
    { name: 'Bitwarden',           category: 'Security',      credAnchor: 'cred-card-api-keys' },
    { name: 'VPN',                 category: 'Security',      credAnchor: null },
    { name: 'Network Scanner',     category: 'Security',      credAnchor: null },
    // Hardware
    { name: 'Printer / Scanner',   category: 'Hardware',      credAnchor: null },
    { name: 'Game Launcher',       category: 'Hardware',      credAnchor: null },
    // Finance
    { name: 'Invoice / Receipt',   category: 'Finance',       credAnchor: null },
    // Communication
    { name: 'Zoom / Google Meet',  category: 'Communication', credAnchor: 'cred-card-api-keys' },
    { name: 'Phone Calls',         category: 'Communication', credAnchor: null },
    // Productivity (second wave)
    { name: 'Notion',              category: 'Productivity',  credAnchor: 'cred-card-api-keys' },
    { name: 'Obsidian',            category: 'Productivity',  credAnchor: null },
    { name: 'Todoist / Tasks',     category: 'Productivity',  credAnchor: 'cred-card-api-keys' },
    { name: 'Time Tracker',        category: 'Productivity',  credAnchor: 'cred-card-api-keys' },
    // Social / Content (second wave)
    { name: 'YouTube',             category: 'Social',        credAnchor: 'cred-card-api-keys' },
    { name: 'Reddit',              category: 'Social',        credAnchor: 'cred-card-api-keys' },
    { name: 'Twitter / X',         category: 'Social',        credAnchor: 'cred-card-api-keys' },
    { name: 'RSS Monitor',         category: 'Social',        credAnchor: null },
    // Creative (second wave)
    { name: 'GIMP / Darktable',    category: 'Creative',      credAnchor: null },
    { name: 'Blender',             category: 'Creative',      credAnchor: null },
    { name: 'Figma',               category: 'Creative',      credAnchor: 'cred-card-api-keys' },
    { name: 'FFmpeg',              category: 'Creative',      credAnchor: null },
    // Smart Home (second wave)
    { name: 'Home Assistant',      category: 'Smart Home',    credAnchor: 'cred-card-api-keys' },
    // Health (later)
    { name: 'Health',              category: 'Health',        credAnchor: 'cred-card-api-keys' },
  ];

  /* Returns a Map<category, service[]> in insertion order. */
  function byCategory() {
    var map = new Map();
    for (var i = 0; i < SERVICES.length; i++) {
      var svc = SERVICES[i];
      if (!map.has(svc.category)) map.set(svc.category, []);
      map.get(svc.category).push(svc);
    }
    return map;
  }

  return { SERVICES: SERVICES, byCategory: byCategory };
}));
