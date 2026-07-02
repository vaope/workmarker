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
        let routeCalls = 0;
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
            routePropose: () => {
              routeCalls += 1;
              return new Promise(() => {});
            },
            onShowCapture: (cb) => { onShowCapture = cb; },
            onArchived: () => {},
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
          if (routeCalls !== 1) {
            throw new Error(`re-show should not reset or resubmit; routeCalls=${routeCalls}`);
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
            routePropose: async () => ({
              ok: true,
              proposal,
              selected_project: {project_id: 'p', title: 'Project', path: 'project.md'},
              route: {reason: 'matched project'},
              low_confidence: false,
            }),
            commit: async () => {
              commitCalls += 1;
              return {ok: true};
            },
            onShowCapture: (cb) => { onShowCapture = cb; },
            onArchived: () => {},
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
          await Promise.resolve();

          if (elements['cap-confirm'].classList.contains('hidden')) {
            throw new Error('confirm card should be visible before committing');
          }
          await elements['ccc-confirm'].listeners.click();

          if (!elements['cap-confirm'].classList.contains('hidden')) {
            throw new Error('confirm card should be hidden after successful commit');
          }
          if (elements['cap-input-area'].classList.contains('hidden')) {
            throw new Error('input area should be visible after successful commit');
          }
          if (elements['cap-submit'].disabled) {
            throw new Error('submit button should be enabled after successful commit');
          }

          onShowCapture();

          if (!elements['cap-confirm'].classList.contains('hidden')) {
            throw new Error('re-show after successful commit should not preserve stale card');
          }
          if (commitCalls !== 1) {
            throw new Error(`expected one commit, got ${commitCalls}`);
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
