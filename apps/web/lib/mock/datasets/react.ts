import type { RepositoryDataset } from '@/lib/types'
import { code, createCiter, file } from '@/lib/mock/helpers'

const files = [
  file({
    path: 'packages/react/index.js',
    group: 'entry',
    imports: ['packages/react/src/ReactHooks.js', 'packages/react/src/jsx/ReactJSXElement.js'],
    exports: ['useState', 'useEffect', 'createElement', 'Component'],
    summary: 'Public entry for the `react` package. Re-exports hooks, element creation and base classes.',
    content: code(`
      /**
       * Copyright (c) Meta Platforms, Inc. and affiliates.
       *
       * This source code is licensed under the MIT license found in the
       * LICENSE file in the root directory of this source tree.
       */

      export {
        Children,
        Component,
        Fragment,
        PureComponent,
        StrictMode,
        Suspense,
        createElement,
        createContext,
        forwardRef,
        lazy,
        memo,
        useCallback,
        useContext,
        useEffect,
        useMemo,
        useReducer,
        useRef,
        useState,
        use,
      } from './src/ReactClient';
    `),
  }),
  file({
    path: 'packages/react/src/ReactHooks.js',
    group: 'core',
    imports: ['packages/react/src/ReactSharedInternals.js'],
    exports: ['useState', 'useEffect', 'useReducer', 'useContext'],
    summary: 'Hook entry points. Each hook forwards to the dispatcher installed by the active renderer.',
    content: code(`
      import ReactSharedInternals from './ReactSharedInternals';

      function resolveDispatcher() {
        const dispatcher = ReactSharedInternals.H;
        if (__DEV__) {
          if (dispatcher === null) {
            console.error(
              'Invalid hook call. Hooks can only be called inside of the body of a function component.',
            );
          }
        }
        return dispatcher;
      }

      export function useState(initialState) {
        const dispatcher = resolveDispatcher();
        return dispatcher.useState(initialState);
      }

      export function useReducer(reducer, initialArg, init) {
        const dispatcher = resolveDispatcher();
        return dispatcher.useReducer(reducer, initialArg, init);
      }

      export function useEffect(create, deps) {
        const dispatcher = resolveDispatcher();
        return dispatcher.useEffect(create, deps);
      }

      export function useContext(Context) {
        const dispatcher = resolveDispatcher();
        return dispatcher.useContext(Context);
      }
    `),
  }),
  file({
    path: 'packages/react/src/ReactSharedInternals.js',
    group: 'data',
    imports: [],
    exports: ['ReactSharedInternals'],
    summary: 'Mutable shared state between `react` and renderers, including the current hooks dispatcher.',
    content: code(`
      const ReactSharedInternals = {
        H: null, // current hooks dispatcher
        A: null, // current async dispatcher
        T: null, // current transition
        S: null, // onStartTransitionFinish
      };

      export default ReactSharedInternals;
    `),
  }),
  file({
    path: 'packages/react/src/jsx/ReactJSXElement.js',
    group: 'core',
    imports: [],
    exports: ['createElement', 'jsx'],
    summary: 'Creates React element objects from JSX calls.',
    content: code(`
      const REACT_ELEMENT_TYPE = Symbol.for('react.transitional.element');

      function ReactElement(type, key, props) {
        return {
          $$typeof: REACT_ELEMENT_TYPE,
          type,
          key,
          props,
        };
      }

      export function createElement(type, config, ...children) {
        const props = {};
        let key = null;

        if (config != null) {
          if (config.key !== undefined) key = '' + config.key;
          for (const propName in config) {
            if (propName !== 'key') props[propName] = config[propName];
          }
        }

        if (children.length === 1) props.children = children[0];
        else if (children.length > 1) props.children = children;

        return ReactElement(type, key, props);
      }
    `),
  }),
  file({
    path: 'packages/react-reconciler/src/ReactFiberWorkLoop.js',
    group: 'service',
    imports: ['packages/react-reconciler/src/ReactFiberBeginWork.js', 'packages/react-reconciler/src/ReactFiberThrow.js'],
    exports: ['scheduleUpdateOnFiber', 'performWorkOnRoot'],
    summary: 'The render/commit loop. Schedules work on fibers and handles thrown values during render.',
    content: code(`
      import { beginWork } from './ReactFiberBeginWork';
      import { throwException } from './ReactFiberThrow';

      let workInProgress = null;

      export function scheduleUpdateOnFiber(root, fiber, lane) {
        markRootUpdated(root, lane);
        ensureRootIsScheduled(root);
      }

      function workLoopSync() {
        while (workInProgress !== null) {
          performUnitOfWork(workInProgress);
        }
      }

      function performUnitOfWork(unitOfWork) {
        const current = unitOfWork.alternate;
        const next = beginWork(current, unitOfWork, renderLanes);

        unitOfWork.memoizedProps = unitOfWork.pendingProps;
        if (next === null) {
          completeUnitOfWork(unitOfWork);
        } else {
          workInProgress = next;
        }
      }

      function handleThrow(root, thrownValue) {
        const erroredWork = workInProgress;
        throwException(root, erroredWork.return, erroredWork, thrownValue, renderLanes);
        completeUnitOfWork(erroredWork);
      }
    `),
  }),
  file({
    path: 'packages/react-reconciler/src/ReactFiberBeginWork.js',
    group: 'service',
    imports: ['packages/react-reconciler/src/ReactFiberHooks.js'],
    exports: ['beginWork'],
    summary: 'Begins rendering a fiber based on its tag (function component, host component, Suspense, \u2026).',
    content: code(`
      import { renderWithHooks } from './ReactFiberHooks';

      export function beginWork(current, workInProgress, renderLanes) {
        switch (workInProgress.tag) {
          case FunctionComponent: {
            const Component = workInProgress.type;
            const nextChildren = renderWithHooks(
              current,
              workInProgress,
              Component,
              workInProgress.pendingProps,
            );
            reconcileChildren(current, workInProgress, nextChildren, renderLanes);
            return workInProgress.child;
          }
          case HostComponent:
            return updateHostComponent(current, workInProgress, renderLanes);
          case SuspenseComponent:
            return updateSuspenseComponent(current, workInProgress, renderLanes);
        }
      }
    `),
  }),
  file({
    path: 'packages/react-reconciler/src/ReactFiberHooks.js',
    group: 'service',
    imports: ['packages/react/src/ReactSharedInternals.js'],
    exports: ['renderWithHooks'],
    summary: 'Installs the mount or update hooks dispatcher and renders a function component.',
    content: code(`
      import ReactSharedInternals from 'shared/ReactSharedInternals';

      export function renderWithHooks(current, workInProgress, Component, props) {
        ReactSharedInternals.H =
          current === null || current.memoizedState === null
            ? HooksDispatcherOnMount
            : HooksDispatcherOnUpdate;

        const children = Component(props);

        ReactSharedInternals.H = ContextOnlyDispatcher;
        return children;
      }
    `),
  }),
  file({
    path: 'packages/react-reconciler/src/ReactFiberThrow.js',
    group: 'service',
    imports: [],
    exports: ['throwException'],
    summary: 'Routes thrown values to the nearest Suspense boundary (promises) or error boundary (errors).',
    content: code(`
      export function throwException(root, returnFiber, sourceFiber, value, rootRenderLanes) {
        sourceFiber.flags |= Incomplete;

        if (value !== null && typeof value === 'object' && typeof value.then === 'function') {
          // This is a wakeable. The component suspended.
          const suspenseBoundary = getSuspenseHandler();
          if (suspenseBoundary !== null) {
            attachPingListener(root, value, rootRenderLanes);
            suspenseBoundary.flags |= ShouldCapture;
            return;
          }
        }

        // Otherwise, find the nearest error boundary
        let workInProgress = returnFiber;
        do {
          if (workInProgress.tag === ClassComponent && typeof workInProgress.type.getDerivedStateFromError === 'function') {
            workInProgress.flags |= ShouldCapture;
            const update = createClassErrorUpdate(rootRenderLanes);
            enqueueCapturedUpdate(workInProgress, update);
            return;
          }
          workInProgress = workInProgress.return;
        } while (workInProgress !== null);
      }
    `),
  }),
  file({
    path: 'package.json',
    content: code(`
      {
        "private": true,
        "workspaces": ["packages/*"],
        "scripts": {
          "build": "node ./scripts/rollup/build-all-release-channels.js",
          "test": "node ./scripts/jest/jest-cli.js",
          "lint": "node ./scripts/tasks/eslint.js"
        }
      }
    `),
  }),
]

const cite = createCiter(files)

export const reactDataset: RepositoryDataset = {
  files,
  insights: {
    summary:
      'React is split into a renderer-agnostic public package (packages/react) and the reconciler (packages/react-reconciler). The public package exposes hooks that forward to a dispatcher stored in ReactSharedInternals. During render, the reconciler installs a mount or update dispatcher, walks the fiber tree in the work loop, and routes thrown values to Suspense or error boundaries. This sample index covers a representative subset of the monorepo.',
    entryPoints: [
      { path: 'packages/react/index.js', name: 'react', description: 'Public API surface of the react package.' },
    ],
    keyModules: [
      { path: 'packages/react/src/ReactHooks.js', name: 'Hooks', description: 'Forward hook calls to the active dispatcher.' },
      { path: 'packages/react-reconciler/src/ReactFiberWorkLoop.js', name: 'Work loop', description: 'Render and commit scheduling.' },
      { path: 'packages/react-reconciler/src/ReactFiberThrow.js', name: 'throwException', description: 'Suspense and error boundary routing.' },
    ],
  },
  answers: [
    {
      keywords: ['error', 'exception', 'throw', 'boundary'],
      content: `When a component throws during render, the work loop calls \`handleThrow()\`, which delegates to \`throwException()\` in \`ReactFiberThrow.js\`.

- If the thrown value is **thenable**, the component *suspended* and the nearest Suspense boundary is marked with \`ShouldCapture\`.
- Otherwise React walks up \`fiber.return\` to find a **class component** implementing \`getDerivedStateFromError\`, and enqueues an error update on it.

~~~js
if (typeof workInProgress.type.getDerivedStateFromError === 'function') {
  workInProgress.flags |= ShouldCapture;
}
~~~`,
      citations: [
        cite('packages/react-reconciler/src/ReactFiberWorkLoop.js', 'function handleThrow', 5),
        cite('packages/react-reconciler/src/ReactFiberThrow.js', 'export function throwException', 24),
      ],
    },
    {
      keywords: ['hook', 'usestate', 'state', 'dispatcher'],
      content: `Hooks in \`packages/react\` contain almost no logic. \`useState\` resolves the current dispatcher from \`ReactSharedInternals.H\` and forwards the call. The reconciler swaps that dispatcher in \`renderWithHooks()\` depending on whether the component is mounting or updating.`,
      citations: [
        cite('packages/react/src/ReactHooks.js', 'function resolveDispatcher', 12),
        cite('packages/react-reconciler/src/ReactFiberHooks.js', 'export function renderWithHooks', 11),
      ],
    },
  ],
  fallbackAnswer: {
    content: `This sample index only covers part of the React monorepo. The indexed files explain the **public hooks API**, **element creation**, and the **fiber work loop**. Try asking how errors or hooks are handled.`,
    citations: [cite('packages/react/index.js', 'export {', 10)],
  },
}
