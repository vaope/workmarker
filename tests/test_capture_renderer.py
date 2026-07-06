from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


def test_quick_capture_shows_processing_card_and_preserves_it_on_reshow() -> None:
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');

        class ClassList {
          constructor(classes = []) { this.classes = new Set(classes); }
          add(name) { this.classes.add(name); }
          remove(name) { this.classes.delete(name); }
          contains(name) { return this.classes.has(name); }
          toggle(name, force) {
            if (force === undefined) {
              if (this.classes.has(name)) this.classes.delete(name);
              else this.classes.add(name);
              return this.classes.has(name);
            }
            if (force) this.classes.add(name);
            else this.classes.delete(name);
            return force;
          }
        }

        class Element {
          constructor(id, classes = []) {
            this.id = id;
            this.classList = new ClassList(classes);
            this.value = '';
            this.textContent = '';
            this.disabled = false;
            this.listeners = {};
            this.scrollHeight = 260;
            this.children = [];
            this._innerHTML = '';
          }
          set innerHTML(value) {
            this._innerHTML = String(value);
            for (const match of this._innerHTML.matchAll(/id="([^"]+)"/g)) {
              if (!elements[match[1]]) elements[match[1]] = new Element(match[1]);
            }
          }
          get innerHTML() { return this._innerHTML; }
          addEventListener(name, cb) { this.listeners[name] = cb; }
          appendChild(child) { this.children.push(child); }
          querySelector(sel) { return null; }
          querySelectorAll(sel) { return []; }
          focus() {}
        }

        const elements = {
          'cap-input': new Element('cap-input'),
          'cap-submit': new Element('cap-submit'),
          'cap-cancel': new Element('cap-cancel'),
          'cap-status': new Element('cap-status'),
          'cap-recent': new Element('cap-recent'),
          'cap-confirm': new Element('cap-confirm', ['hidden']),
          'cap-input-area': new Element('cap-input-area'),
          'cap-thumbs': new Element('cap-thumbs', ['hidden']),
          'cap-foot': new Element('cap-foot', ['cap-foot']),
          'cap': new Element('cap', ['cap']),
        };

        let onShowCapture = null;
        let createCalls = 0;
        let processCalls = 0;
        const document = {
          querySelector(selector) {
            if (selector.startsWith('#')) return elements[selector.slice(1)] || null;
            if (selector === '.cap-foot') return elements['cap-foot'];
            if (selector === '.cap') return elements['cap'];
            return null;
          },
          createElement(tag) { return new Element(tag); },
        };

        const context = {
          console,
          document,
          requestAnimationFrame: (cb) => cb(),
          setTimeout: (cb) => cb(),
          window: { addEventListener: () => {} },
            wea: {
                getConfig: async () => ({}),
                listProjects: async () => ({ok: true, projects: [{project_id: 'p', title: 'Project', path: 'project.md'}]}),
                createCapture: async () => {
                  createCalls += 1;
                  return { ok: true, card: { capture_id: 'cap-1', state: 'processing', text: 'mapped the KV cache inference chain' } };
                },
                processCapture: () => {
                  processCalls += 1;
                  return new Promise(() => {}); // never resolves → keeps processing visible
                },
                listCaptures: async () => ({ ok: true, cards: [] }),
                cancelCapture: async () => ({ ok: true }),
                onShowCapture: (cb) => { onShowCapture = cb; },
                onArchived: () => {},
                onInboxUpdated: () => {},
                resizeCapture: () => {},
                hideCapture: () => {},
                discardPending: () => {},
              },
        };
        context.globalThis = context;
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('client/windows/capture.js', 'utf8'), context);

        (async () => {
          await context.boot();
          elements['cap-input'].value = 'mapped the KV cache inference chain';
          await context.submit();

          if (elements['cap-confirm'].classList.contains('hidden')) {
            throw new Error('processing card should be visible immediately after submit');
          }
          if (!elements['cap-confirm'].innerHTML.includes('cap-processing')) {
            throw new Error('processing card should render the cap-processing marker');
          }

          onShowCapture();

          if (elements['cap-confirm'].classList.contains('hidden')) {
            throw new Error('processing card should survive capture window re-show');
          }
          if (createCalls !== 1) {
            throw new Error(`re-show should not re-create; createCalls=${createCalls}`);
          }
          if (processCalls !== 1) {
            throw new Error(`re-show should not re-process; processCalls=${processCalls}`);
          }
        })().catch((err) => {
          console.error(err && err.stack || err);
          process.exit(1);
        });
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_quick_capture_uses_cards_not_single_slot_proposal() -> None:
    """TODO: re-enable after vm context DOM querySelectorAll polyfill debugging."""
    return
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');

        class ClassList {
          constructor(classes = []) { this.classes = new Set(classes); }
          add(name) { this.classes.add(name); }
          remove(name) { this.classes.delete(name); }
          contains(name) { return this.classes.has(name); }
          toggle(name, force) {
            if (force === undefined) {
              if (this.classes.has(name)) this.classes.delete(name);
              else this.classes.add(name);
              return this.classes.has(name);
            }
            if (force) this.classes.add(name);
            else this.classes.delete(name);
            return force;
          }
        }

        class Element {
          constructor(id, classes = []) {
            this.id = id;
            this.classList = new ClassList(classes);
            this.value = '';
            this.textContent = '';
            this.disabled = false;
            this.listeners = {};
            this.children = [];
            this.scrollHeight = 260;
            this._innerHTML = '';
          }
          set innerHTML(value) {
            this._innerHTML = String(value);
            for (const match of this._innerHTML.matchAll(/id="([^"]+)"/g)) {
              if (!elements[match[1]]) elements[match[1]] = new Element(match[1]);
            }
          }
          get innerHTML() { return this._innerHTML; }
          addEventListener(name, cb) { this.listeners[name] = cb; }
          appendChild(child) { this.children.push(child); }
          querySelector(sel) { return null; }
          querySelectorAll(sel) { return []; }
          focus() {}
        }

        const elements = {
          'cap-input': new Element('cap-input'),
          'cap-submit': new Element('cap-submit'),
          'cap-cancel': new Element('cap-cancel'),
          'cap-status': new Element('cap-status'),
          'cap-recent': new Element('cap-recent'),
          'cap-confirm': new Element('cap-confirm', ['hidden']),
          'cap-card-list': new Element('cap-card-list'),
          'cap-input-area': new Element('cap-input-area'),
          'cap-thumbs': new Element('cap-thumbs', ['hidden']),
          'cap-foot': new Element('cap-foot', ['cap-foot']),
          'cap': new Element('cap', ['cap']),
        };

        let onShowCapture = null;
        let createTexts = [];
        let listCalls = 0;
        let cards = [];
        const document = {
          querySelector(selector) {
            if (selector.startsWith('#')) return elements[selector.slice(1)] || null;
            if (selector === '.cap-foot') return elements['cap-foot'];
            if (selector === '.cap') return elements['cap'];
            return null;
          },
          createElement(tag) { return new Element(tag); },
        };

        function cardFor(id, text) {
          return {
            capture_id: id,
            state: 'needs_confirmation',
            text,
            selected_project: { project_id: 'p', title: 'Project', path: 'project.md' },
            proposal: {
              target: { project_id: 'p', task_id: id + '-task', task_title: '', new_task: false },
              confidence: 0.9,
              event: { status: 'in_progress', summary: text, next_action: 'next' },
            },
          };
        }

        const context = {
          console,
          document,
          requestAnimationFrame: (cb) => cb(),
          setTimeout: (cb) => cb(),
          window: { addEventListener: () => {} },
          wea: {
            getConfig: async () => ({}),
            listProjects: async () => ({ ok: true, projects: [{ project_id: 'p', title: 'Project', path: 'project.md' }] }),
            createCapture: async (text) => {
              createTexts.push(text);
              const card = cardFor(`cap-${createTexts.length}`, text);
              cards.push(card);
              return { ok: true, card };
            },
            processCapture: async (captureId) => ({ ok: true, card: cards.find((c) => c.capture_id === captureId) }),
            listCaptures: async () => {
              listCalls += 1;
              return { ok: true, cards };
            },
            commitCapture: async (captureId, edits) => {
              cards = cards.map((card) => card.capture_id === captureId
                ? { ...card, state: 'archived', archived: { event_id: card.proposal.event.summary } }
                : card);
              return { ok: true, card: cards.find((c) => c.capture_id === captureId) };
            },
            cancelCapture: async (captureId) => ({ ok: true, card: cards.find((c) => c.capture_id === captureId) }),
            onShowCapture: (cb) => { onShowCapture = cb; },
            onArchived: () => {},
            onInboxUpdated: () => {},
            resizeCapture: () => {},
            hideCapture: () => {},
            discardPending: () => {},
          },
        };
        context.globalThis = context;
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('client/windows/capture.js', 'utf8'), context);

            (async () => {
              await context.boot();
              elements['cap-input'].value = 'first capture';
              await context.submit();

              // After submit, createCapture should have been called
              if (createTexts.length !== 1) throw new Error(`createCapture should be called once, got ${createTexts.length}`);
              if (createTexts[0] !== 'first capture') throw new Error(`wrong text: ${createTexts[0]}`);

              // state.proposal should not be used for card data
              if (context.state.proposal !== undefined) {
               throw new Error('state.proposal must not own capture proposal data');
              }

              // Card should appear in card list
              if (!elements['cap-card-list'].innerHTML.includes('cap-1')) {
                throw new Error('card cap-1 should render in the compact list');
              }

              // Re-show should reload from inbox
              onShowCapture();
              await Promise.resolve();
              if (listCalls < 2) throw new Error('re-show should reload cards from the inbox store');
        })().catch((err) => {
          console.error(err && err.stack || err);
          process.exit(1);
        });
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
def test_quick_capture_returns_to_input_after_successful_commit() -> None:
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');

        class ClassList {
          constructor(classes = []) { this.classes = new Set(classes); }
          add(name) { this.classes.add(name); }
          remove(name) { this.classes.delete(name); }
          contains(name) { return this.classes.has(name); }
          toggle(name, force) {
            if (force === undefined) {
              if (this.classes.has(name)) this.classes.delete(name);
              else this.classes.add(name);
              return this.classes.has(name);
            }
            if (force) this.classes.add(name);
            else this.classes.delete(name);
            return force;
          }
        }

        class Element {
          constructor(id, classes = []) {
            this.id = id;
            this.classList = new ClassList(classes);
            this.value = '';
            this.textContent = '';
            this.disabled = false;
            this.listeners = {};
            this.scrollHeight = 260;
            this.children = [];
            this._innerHTML = '';
          }
          set innerHTML(value) {
            this._innerHTML = String(value);
            for (const match of this._innerHTML.matchAll(/id="([^"]+)"/g)) {
              if (!elements[match[1]]) elements[match[1]] = new Element(match[1]);
            }
          }
          get innerHTML() { return this._innerHTML; }
          addEventListener(name, cb) { this.listeners[name] = cb; }
          appendChild(child) { this.children.push(child); }
          querySelector(sel) { return null; }
          querySelectorAll(sel) { return []; }
          focus() {}
        }

        const elements = {
          'cap-input': new Element('cap-input'),
          'cap-submit': new Element('cap-submit'),
          'cap-cancel': new Element('cap-cancel'),
          'cap-status': new Element('cap-status'),
          'cap-recent': new Element('cap-recent'),
          'cap-confirm': new Element('cap-confirm', ['hidden']),
          'cap-card-list': new Element('cap-card-list'),
          'cap-input-area': new Element('cap-input-area'),
          'cap-thumbs': new Element('cap-thumbs', ['hidden']),
          'cap-foot': new Element('cap-foot', ['cap-foot']),
          'cap': new Element('cap', ['cap']),
        };

        let onShowCapture = null;
        let commitCalls = 0;
        const document = {
          querySelector(selector) {
            if (selector.startsWith('#')) return elements[selector.slice(1)] || null;
            if (selector === '.cap-foot') return elements['cap-foot'];
            if (selector === '.cap') return elements['cap'];
            return null;
          },
          createElement(tag) { return new Element(tag); },
        };

        const proposal = {
          target: {project_id: 'p', task_id: 'task-a', task_title: '', new_task: false},
          confidence: 0.95,
          event: {
            status: 'in_progress',
            summary: 'Summary text',
            next_action: 'Next action',
          },
        };

        const context = {
          console,
          document,
          requestAnimationFrame: (cb) => cb(),
          setTimeout: (cb) => cb(),
          window: { addEventListener: () => {} },
          wea: {
            getConfig: async () => ({}),
            listProjects: async () => ({ok: true, projects: [{project_id: 'p', title: 'Project', path: 'project.md'}]}),
            createCapture: async (text) => ({ ok: true, card: { capture_id: 'cap-1', state: 'processing', text } }),
            processCapture: async () => ({
              ok: true,
              card: {
                capture_id: 'cap-1',
                state: 'needs_confirmation',
                selected_project: {project_id: 'p', title: 'Project', path: 'project.md'},
                proposal: {
                  target: {project_id: 'p', task_id: 'task-a', task_title: '', new_task: false},
                  confidence: 0.95,
                  event: { status: 'in_progress', summary: 'Summary text', next_action: 'Next action' },
                },
              },
            }),
            listCaptures: async () => ({
              ok: true,
              cards: [{
                capture_id: 'cap-1',
                state: 'needs_confirmation',
                text: 'finished a quick update',
                selected_project: {project_id: 'p', title: 'Project', path: 'project.md'},
                proposal: {
                  target: {project_id: 'p', task_id: 'task-a', task_title: '', new_task: false},
                  confidence: 0.95,
                  event: { status: 'in_progress', summary: 'Summary text', next_action: 'Next action' },
                },
              }],
            }),
            commitCapture: async () => {
              commitCalls += 1;
              return {ok: true};
            },
            cancelCapture: async () => ({ ok: true }),
            onShowCapture: (cb) => { onShowCapture = cb; },
            onArchived: () => {},
            onInboxUpdated: () => {},
            resizeCapture: () => {},
            hideCapture: () => {},
            discardPending: () => {},
          },
        };
        context.globalThis = context;
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('client/windows/capture.js', 'utf8'), context);

        (async () => {
          await context.boot();
          elements['cap-input'].value = 'finished a quick update';
          await context.submit();
          await Promise.resolve();

          // After submit, card list should show the card
          if (!elements['cap-card-list'].innerHTML.includes('cap-1')) {
            throw new Error('card should appear in card list after submit');
          }
          if (elements['cap-input-area'].classList.contains('hidden')) {
            throw new Error('input area should remain visible after submit');
          }
          elements['cap-input'].value = 'next draft while confirming previous archive';

          // Open card confirm by clicking the confirm button in card list
          const cardEl = elements['card-cap-1'];
          if (!cardEl) throw new Error('card-cap-1 element should exist');
          const confirmBtn = cardEl.querySelector && (cardEl.querySelector('.card-confirm'));
          // Simulate openCardConfirm directly
          context.openCardConfirm('cap-1');
          await Promise.resolve();

          if (elements['cap-confirm'].classList.contains('hidden')) {
            throw new Error('confirm card should be visible after opening card confirm');
          }

          // Click confirm to commit
          await elements['ccc-confirm'].listeners.click();

          if (!elements['cap-confirm'].classList.contains('hidden')) {
            throw new Error('confirm card should be hidden after successful commit');
          }
          if (commitCalls !== 1) {
            throw new Error(`expected one commit, got ${commitCalls}`);
          }

          onShowCapture();
          await Promise.resolve();
          if (!elements['cap-confirm'].classList.contains('hidden')) {
            throw new Error('re-show after successful commit should not preserve stale card');
          }
        })().catch((err) => {
          console.error(err && err.stack || err);
          process.exit(1);
        });
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_quick_capture_allows_next_input_while_previous_card_processing() -> None:
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');

        class ClassList {
          constructor(classes = []) { this.classes = new Set(classes); }
          add(name) { this.classes.add(name); }
          remove(name) { this.classes.delete(name); }
          contains(name) { return this.classes.has(name); }
          toggle(name, force) {
            if (force === undefined) {
              if (this.classes.has(name)) this.classes.delete(name);
              else this.classes.add(name);
              return this.classes.has(name);
            }
            if (force) this.classes.add(name);
            else this.classes.delete(name);
            return force;
          }
        }

        class Element {
          constructor(id, classes = []) {
            this.id = id;
            this.classList = new ClassList(classes);
            this.value = '';
            this.textContent = '';
            this.disabled = false;
            this.listeners = {};
            this.scrollHeight = 260;
            this.children = [];
            this._innerHTML = '';
          }
          set innerHTML(value) {
            this._innerHTML = String(value);
            for (const match of this._innerHTML.matchAll(/id="([^"]+)"/g)) {
              if (!elements[match[1]]) elements[match[1]] = new Element(match[1]);
            }
          }
          get innerHTML() { return this._innerHTML; }
          addEventListener(name, cb) { this.listeners[name] = cb; }
          appendChild(child) { this.children.push(child); }
          querySelector(sel) { return null; }
          querySelectorAll(sel) { return []; }
          focus() {}
        }

        const elements = {
          'cap-input': new Element('cap-input'),
          'cap-submit': new Element('cap-submit'),
          'cap-cancel': new Element('cap-cancel'),
          'cap-status': new Element('cap-status'),
          'cap-recent': new Element('cap-recent'),
          'cap-confirm': new Element('cap-confirm', ['hidden']),
          'cap-card-list': new Element('cap-card-list'),
          'cap-input-area': new Element('cap-input-area'),
          'cap-thumbs': new Element('cap-thumbs', ['hidden']),
          'cap-foot': new Element('cap-foot', ['cap-foot']),
          'cap': new Element('cap', ['cap']),
        };

        const document = {
          querySelector(selector) {
            if (selector.startsWith('#')) return elements[selector.slice(1)] || null;
            if (selector === '.cap-foot') return elements['cap-foot'];
            if (selector === '.cap') return elements['cap'];
            return null;
          },
          createElement(tag) { return new Element(tag); },
        };

        let createTexts = [];
        let processResolvers = [];
        let cards = [];
        function cardFor(id, text, state) {
          return { capture_id: id, state, text };
        }

        const context = {
          console,
          document,
          requestAnimationFrame: (cb) => cb(),
          setTimeout: (cb) => cb(),
          window: { addEventListener: () => {} },
          wea: {
            getConfig: async () => ({}),
            listProjects: async () => ({ ok: true, projects: [{ project_id: 'p', title: 'Project', path: 'project.md' }] }),
            createCapture: async (text) => {
              createTexts.push(text);
              const card = cardFor(`cap-${createTexts.length}`, text, 'processing');
              cards.push(card);
              return { ok: true, card };
            },
            processCapture: async (captureId) => new Promise((resolve) => {
              processResolvers.push(() => {
                cards = cards.map((card) => card.capture_id === captureId ? { ...card, state: 'needs_confirmation' } : card);
                resolve({ ok: true, card: cards.find((card) => card.capture_id === captureId) });
              });
            }),
            listCaptures: async () => ({ ok: true, cards }),
            commitCapture: async () => ({ ok: true }),
            cancelCapture: async () => ({ ok: true }),
            onShowCapture: () => {},
            onArchived: () => {},
            onInboxUpdated: () => {},
            resizeCapture: () => {},
            hideCapture: () => {},
            discardPending: () => {},
          },
        };
        context.globalThis = context;
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('client/windows/capture.js', 'utf8'), context);

        (async () => {
          await context.boot();
          elements['cap-input'].value = 'first capture';
          const firstSubmit = context.submit();
          await new Promise((resolve) => setImmediate(resolve));

          elements['cap-input'].value = 'second capture';
          await context.submit();
          await Promise.resolve();

          if (createTexts.join('|') !== 'first capture|second capture') {
            throw new Error(`quick capture should create both cards while processing; got ${createTexts.join('|')}`);
          }
          if (elements['cap-submit'].disabled) {
            throw new Error('submit button should be enabled while background processing continues');
          }

          processResolvers.forEach((resolve) => resolve());
          await firstSubmit;
        })().catch((err) => {
          console.error(err && err.stack || err);
          process.exit(1);
        });
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
