/* Emby Keeper WebUI — shared utilities and Vue/Naive UI references.
 * Exposed as window.EK.* . Build-free: classic script, IIFE-scoped.
 */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});

  // ---- Shared Vue / Naive UI handles (re-destructured per-module from these) ----
  EK.Vue = window.Vue;
  EK.VueRouter = window.VueRouter;
  EK.naiveUI = window.naiveUI;

  // ---- Generic helpers ----
  function formatDate(value) {
    return value ? new Date(value).toLocaleString() : '-';
  }

  function trimText(value) {
    return typeof value === 'string' ? value.trim() : value;
  }

  function optionalText(value) {
    const trimmed = trimText(value);
    return trimmed || null;
  }

  function splitListText(value) {
    if (!value) return [];
    return String(value).split(/[\n,，]+/).map((item) => item.trim()).filter(Boolean);
  }

  function hasWhitespace(value) {
    return /\s/.test(value || '');
  }

  function isIntegerInRange(value, min, max) {
    return Number.isInteger(value) && value >= min && value <= max;
  }

  function normalizeBot(value) {
    let bot = trimText(value) || '';
    bot = bot.replace(/^https?:\/\/t\.me\//i, '').split('?', 1)[0].replace(/^@/, '').replace(/\/+$/, '').trim();
    return bot;
  }

  // ---- Status → Naive UI tag type mappers ----
  function runStatusType(status) {
    if (status === 'success') return 'success';
    if (status === 'running' || status === 'initializing') return 'warning';
    if (status === 'fail' || status === 'error') return 'error';
    if (status === 'cancelled') return 'default';
    return 'info';
  }

  // ---- UI action wrappers ----
  function responseMessage(res, fallback) {
    return res && res.message ? res.message : fallback;
  }

  async function runUiAction({ setLoading, action, message, success, refresh }) {
    if (setLoading) setLoading(true);
    try {
      const res = await action();
      const text = typeof success === 'function' ? success(res) : success;
      if (text) message.success(text);
      if (refresh) await refresh(res);
      return res;
    } catch (e) {
      message.error(e.message);
      return null;
    } finally {
      if (setLoading) setLoading(false);
    }
  }

  async function copyText(text, message, successText) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }
      if (message && successText) message.success(successText);
    } catch (e) {
      if (message) message.error('复制失败');
    }
  }

  function downloadJson(filename, data) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  EK.util = {
    formatDate, trimText, optionalText, splitListText, hasWhitespace,
    isIntegerInRange, normalizeBot, runStatusType,
    responseMessage, runUiAction, copyText, downloadJson,
  };
}());
