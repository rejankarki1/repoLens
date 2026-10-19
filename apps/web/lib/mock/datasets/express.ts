import type { RepositoryDataset } from '@/lib/types'
import { code, createCiter, file } from '@/lib/mock/helpers'

const files = [
  file({
    path: 'index.js',
    group: 'entry',
    imports: ['lib/express.js'],
    exports: ['module.exports'],
    summary: 'Package entry point. Re-exports the application factory from lib/express.js.',
    content: code(`
      /*!
       * express
       * Copyright(c) 2009-2013 TJ Holowaychuk
       * Copyright(c) 2013 Roman Shtylman
       * Copyright(c) 2014-2015 Douglas Christopher Wilson
       * MIT Licensed
       */

      'use strict';

      module.exports = require('./lib/express');
    `),
  }),
  file({
    path: 'lib/express.js',
    group: 'core',
    imports: ['lib/application.js', 'lib/router/index.js', 'lib/router/route.js', 'lib/request.js', 'lib/response.js'],
    exports: ['createApplication', 'application', 'request', 'response', 'Route', 'Router', 'json', 'static', 'urlencoded'],
    summary: 'Defines createApplication(), which mixes the application prototype and EventEmitter into a request handler function.',
    content: code(`
      'use strict';

      /**
       * Module dependencies.
       */

      var bodyParser = require('body-parser')
      var EventEmitter = require('events').EventEmitter;
      var mixin = require('merge-descriptors');
      var proto = require('./application');
      var Route = require('./router/route');
      var Router = require('./router');
      var req = require('./request');
      var res = require('./response');

      /**
       * Expose \`createApplication()\`.
       */

      exports = module.exports = createApplication;

      /**
       * Create an express application.
       *
       * @return {Function}
       * @api public
       */

      function createApplication() {
        var app = function(req, res, next) {
          app.handle(req, res, next);
        };

        mixin(app, EventEmitter.prototype, false);
        mixin(app, proto, false);

        // expose the prototype that will get set on requests
        app.request = Object.create(req, {
          app: { configurable: true, enumerable: true, writable: true, value: app }
        })

        // expose the prototype that will get set on responses
        app.response = Object.create(res, {
          app: { configurable: true, enumerable: true, writable: true, value: app }
        })

        app.init();
        return app;
      }

      /**
       * Expose the prototypes.
       */

      exports.application = proto;
      exports.request = req;
      exports.response = res;

      /**
       * Expose constructors.
       */

      exports.Route = Route;
      exports.Router = Router;

      /**
       * Expose middleware
       */

      exports.json = bodyParser.json
      exports.static = require('serve-static');
      exports.urlencoded = bodyParser.urlencoded
    `),
  }),
  file({
    path: 'lib/application.js',
    group: 'core',
    imports: ['lib/router/index.js', 'lib/middleware/init.js', 'lib/middleware/query.js', 'lib/view.js', 'lib/utils.js'],
    exports: ['app.init', 'app.handle', 'app.use', 'app.route', 'app.render', 'app.listen'],
    summary: 'The application prototype: settings, lazy router creation, middleware mounting, request handling, and listen().',
    content: code(`
      'use strict';

      var finalhandler = require('finalhandler');
      var Router = require('./router');
      var methods = require('methods');
      var middleware = require('./middleware/init');
      var query = require('./middleware/query');
      var debug = require('debug')('express:application');
      var View = require('./view');
      var http = require('http');
      var compileETag = require('./utils').compileETag;
      var compileQueryParser = require('./utils').compileQueryParser;
      var flatten = require('array-flatten');

      var app = exports = module.exports = {};

      /**
       * Initialize the server.
       *
       *   - setup default configuration
       *   - setup default middleware
       *   - setup route reflection methods
       */

      app.init = function init() {
        this.cache = {};
        this.engines = {};
        this.settings = {};

        this.defaultConfiguration();
      };

      app.defaultConfiguration = function defaultConfiguration() {
        var env = process.env.NODE_ENV || 'development';

        this.enable('x-powered-by');
        this.set('etag', 'weak');
        this.set('env', env);
        this.set('query parser', 'extended');
        this.set('subdomain offset', 2);
        this.set('trust proxy', false);

        debug('booting in %s mode', env);
      };

      /**
       * lazily adds the base router if it has not yet been added.
       */

      app.lazyrouter = function lazyrouter() {
        if (!this._router) {
          this._router = new Router({
            caseSensitive: this.enabled('case sensitive routing'),
            strict: this.enabled('strict routing')
          });

          this._router.use(query(this.get('query parser fn')));
          this._router.use(middleware.init(this));
        }
      };

      /**
       * Dispatch a req, res pair into the application. Starts pipeline processing.
       *
       * If no callback is provided, then default error handlers will respond
       * in the event of an error bubbling through the stack.
       */

      app.handle = function handle(req, res, callback) {
        var router = this._router;

        // final handler
        var done = callback || finalhandler(req, res, {
          env: this.get('env'),
          onerror: logerror.bind(this)
        });

        // no routes
        if (!router) {
          debug('no routes defined on app');
          done();
          return;
        }

        router.handle(req, res, done);
      };

      /**
       * Proxy \`Router#use()\` to add middleware to the app router.
       */

      app.use = function use(fn) {
        var offset = 0;
        var path = '/';

        if (typeof fn !== 'function') {
          path = fn;
          offset = 1;
        }

        var fns = flatten(Array.prototype.slice.call(arguments, offset));

        if (fns.length === 0) {
          throw new TypeError('app.use() requires a middleware function')
        }

        this.lazyrouter();
        var router = this._router;

        fns.forEach(function (fn) {
          router.use(path, fn);
        }, this);

        return this;
      };

      app.route = function route(path) {
        this.lazyrouter();
        return this._router.route(path);
      };

      methods.forEach(function(method){
        app[method] = function(path){
          this.lazyrouter();

          var route = this._router.route(path);
          route[method].apply(route, Array.prototype.slice.call(arguments, 1));
          return this;
        };
      });

      /**
       * Listen for connections.
       */

      app.listen = function listen() {
        var server = http.createServer(this);
        return server.listen.apply(server, arguments);
      };

      /**
       * Log error using console.error.
       */

      function logerror(err) {
        if (this.get('env') !== 'test') console.error(err.stack || err.toString());
      }
    `),
  }),
  file({
    path: 'lib/router/index.js',
    group: 'routing',
    imports: ['lib/router/route.js', 'lib/router/layer.js'],
    exports: ['Router', 'proto.handle', 'proto.use', 'proto.route', 'proto.param'],
    summary: 'The Router: a stack of Layers that are matched in order. next() drives the middleware chain and error propagation.',
    content: code(`
      'use strict';

      var Route = require('./route');
      var Layer = require('./layer');
      var methods = require('methods');
      var debug = require('debug')('express:router');
      var parseUrl = require('parseurl');

      var proto = module.exports = function(options) {
        var opts = options || {};

        function router(req, res, next) {
          router.handle(req, res, next);
        }

        Object.setPrototypeOf(router, proto)

        router.params = {};
        router.caseSensitive = opts.caseSensitive;
        router.mergeParams = opts.mergeParams;
        router.strict = opts.strict;
        router.stack = [];

        return router;
      };

      /**
       * Dispatch a req, res into the router.
       */

      proto.handle = function handle(req, res, out) {
        var self = this;
        var idx = 0;
        var stack = self.stack;
        var done = restore(out, req, 'baseUrl', 'next', 'params');

        req.next = next;
        req.baseUrl = req.baseUrl || '';

        next();

        function next(err) {
          var layerError = err === 'route' ? null : err;

          // signal to exit router
          if (layerError === 'router') {
            setImmediate(done, null)
            return
          }

          // no more matching layers
          if (idx >= stack.length) {
            setImmediate(done, layerError);
            return;
          }

          var path = parseUrl(req).pathname;
          var layer;
          var match;
          var route;

          // find next matching layer
          while (match !== true && idx < stack.length) {
            layer = stack[idx++];
            match = layer.match(path);
            route = layer.route;

            if (match !== true) continue;
            if (!route) continue; // process non-route handlers normally

            if (layerError) {
              // routes do not match with a pending error
              match = false;
              continue;
            }
          }

          // no match
          if (match !== true) {
            return done(layerError);
          }

          req.params = layer.params;

          if (layerError) {
            layer.handle_error(layerError, req, res, next);
          } else {
            layer.handle_request(req, res, next);
          }
        }
      };

      /**
       * Use the given middleware function, with optional path, defaulting to "/".
       */

      proto.use = function use(path, fn) {
        var layer = new Layer(path, {
          sensitive: this.caseSensitive,
          strict: false,
          end: false
        }, fn);

        layer.route = undefined;
        this.stack.push(layer);
        return this;
      };

      /**
       * Create a new Route for the given path.
       */

      proto.route = function route(path) {
        var route = new Route(path);

        var layer = new Layer(path, {
          sensitive: this.caseSensitive,
          strict: this.strict,
          end: true
        }, route.dispatch.bind(route));

        layer.route = route;
        this.stack.push(layer);
        return route;
      };
    `),
  }),
  file({
    path: 'lib/router/layer.js',
    group: 'routing',
    imports: [],
    exports: ['Layer', 'Layer.prototype.match', 'Layer.prototype.handle_error', 'Layer.prototype.handle_request'],
    summary: 'Wraps a single handler with its path matcher. Distinguishes error handlers by their 4-argument arity.',
    content: code(`
      'use strict';

      var pathRegexp = require('path-to-regexp');
      var debug = require('debug')('express:router:layer');

      module.exports = Layer;

      function Layer(path, options, fn) {
        if (!(this instanceof Layer)) {
          return new Layer(path, options, fn);
        }

        debug('new %o', path)
        var opts = options || {};

        this.handle = fn;
        this.name = fn.name || '<anonymous>';
        this.params = undefined;
        this.path = undefined;
        this.regexp = pathRegexp(path, this.keys = [], opts);
      }

      /**
       * Handle the error for the layer.
       *
       * Only functions declared with four arguments
       * (err, req, res, next) are treated as error handlers.
       */

      Layer.prototype.handle_error = function handle_error(error, req, res, next) {
        var fn = this.handle;

        if (fn.length !== 4) {
          // not a standard error handler
          return next(error);
        }

        try {
          fn(error, req, res, next);
        } catch (err) {
          next(err);
        }
      };

      /**
       * Handle the request for the layer.
       */

      Layer.prototype.handle_request = function handle(req, res, next) {
        var fn = this.handle;

        if (fn.length > 3) {
          // not a standard request handler
          return next();
        }

        try {
          fn(req, res, next);
        } catch (err) {
          next(err);
        }
      };

      /**
       * Check if this route matches \`path\`, if so
       * populate \`.params\`.
       */

      Layer.prototype.match = function match(path) {
        var match = this.regexp.exec(path)

        if (!match) {
          this.params = undefined;
          this.path = undefined;
          return false;
        }

        this.params = {};
        this.path = match[0]
        return true;
      };
    `),
  }),
  file({
    path: 'lib/router/route.js',
    group: 'routing',
    imports: ['lib/router/layer.js'],
    exports: ['Route', 'Route.prototype.dispatch', 'Route.prototype.all'],
    summary: 'A Route holds per-HTTP-method handler stacks for a single path and dispatches matching requests.',
    content: code(`
      'use strict';

      var debug = require('debug')('express:router:route');
      var flatten = require('array-flatten');
      var Layer = require('./layer');
      var methods = require('methods');

      module.exports = Route;

      function Route(path) {
        this.path = path;
        this.stack = [];

        debug('new %o', path)

        // route handlers for various http methods
        this.methods = {};
      }

      /**
       * dispatch req, res into this route
       */

      Route.prototype.dispatch = function dispatch(req, res, done) {
        var idx = 0;
        var stack = this.stack;

        if (stack.length === 0) {
          return done();
        }

        var method = req.method.toLowerCase();
        if (method === 'head' && !this.methods['head']) {
          method = 'get';
        }

        req.route = this;
        next();

        function next(err) {
          // signal to exit route
          if (err && err === 'route') {
            return done();
          }

          var layer = stack[idx++];
          if (!layer) {
            return done(err);
          }

          if (layer.method && layer.method !== method) {
            next(err)
          } else if (err) {
            layer.handle_error(err, req, res, next);
          } else {
            layer.handle_request(req, res, next);
          }
        }
      };

      methods.forEach(function(method){
        Route.prototype[method] = function(){
          var handles = flatten(Array.prototype.slice.call(arguments));

          for (var i = 0; i < handles.length; i++) {
            var layer = Layer('/', {}, handles[i]);
            layer.method = method;

            this.methods[method] = true;
            this.stack.push(layer);
          }

          return this;
        };
      });
    `),
  }),
  file({
    path: 'lib/middleware/init.js',
    group: 'middleware',
    imports: [],
    exports: ['init'],
    summary: 'Internal middleware that links req/res to each other and sets the app request/response prototypes.',
    content: code(`
      'use strict';

      /**
       * Initialization middleware, exposing the
       * request and response to each other, as well
       * as defaulting the X-Powered-By header field.
       */

      exports.init = function(app){
        return function expressInit(req, res, next){
          if (app.enabled('x-powered-by')) res.setHeader('X-Powered-By', 'Express');
          req.res = res;
          res.req = req;
          req.next = next;

          Object.setPrototypeOf(req, app.request)
          Object.setPrototypeOf(res, app.response)

          res.locals = res.locals || Object.create(null);

          next();
        };
      };
    `),
  }),
  file({
    path: 'lib/middleware/query.js',
    group: 'middleware',
    imports: [],
    exports: ['query'],
    summary: 'Parses the URL query string into req.query using the configured query parser.',
    content: code(`
      'use strict';

      var merge = require('utils-merge')
      var parseUrl = require('parseurl');
      var qs = require('qs');

      module.exports = function query(options) {
        var opts = merge({}, options)
        var queryparse = qs.parse;

        if (typeof options === 'function') {
          queryparse = options;
          opts = undefined;
        }

        return function query(req, res, next){
          if (!req.query) {
            var val = parseUrl(req).query;
            req.query = queryparse(val, opts);
          }

          next();
        };
      };
    `),
  }),
  file({
    path: 'lib/request.js',
    group: 'core',
    imports: [],
    exports: ['req.get', 'req.accepts', 'req.is', 'req.ip', 'req.protocol', 'req.secure'],
    summary: 'Extends http.IncomingMessage with helpers such as req.get(), req.accepts(), req.ip and req.protocol.',
    content: code(`
      'use strict';

      var accepts = require('accepts');
      var isIP = require('net').isIP;
      var typeis = require('type-is');
      var http = require('http');
      var proxyaddr = require('proxy-addr');

      var req = Object.create(http.IncomingMessage.prototype)

      module.exports = req

      /**
       * Return request header.
       */

      req.get =
      req.header = function header(name) {
        var lc = name.toLowerCase();

        switch (lc) {
          case 'referer':
          case 'referrer':
            return this.headers.referrer || this.headers.referer;
          default:
            return this.headers[lc];
        }
      };

      req.accepts = function(){
        var accept = accepts(this);
        return accept.types.apply(accept, arguments);
      };

      req.is = function is(types) {
        return typeis(this, types);
      };

      defineGetter(req, 'protocol', function protocol(){
        var proto = this.connection.encrypted ? 'https' : 'http';
        var trust = this.app.get('trust proxy fn');

        if (!trust(this.connection.remoteAddress, 0)) {
          return proto;
        }

        var header = this.get('X-Forwarded-Proto') || proto
        return header.split(',')[0].trim()
      });

      defineGetter(req, 'secure', function secure(){
        return this.protocol === 'https';
      });

      defineGetter(req, 'ip', function ip(){
        var trust = this.app.get('trust proxy fn');
        return proxyaddr(this, trust);
      });

      function defineGetter(obj, name, getter) {
        Object.defineProperty(obj, name, {
          configurable: true,
          enumerable: true,
          get: getter
        });
      }
    `),
  }),
  file({
    path: 'lib/response.js',
    group: 'core',
    imports: ['lib/utils.js'],
    exports: ['res.status', 'res.send', 'res.json', 'res.redirect', 'res.render'],
    summary: 'Extends http.ServerResponse with res.status(), res.send(), res.json(), res.redirect() and res.render().',
    content: code(`
      'use strict';

      var Buffer = require('safe-buffer').Buffer
      var http = require('http');
      var setCharset = require('./utils').setCharset;
      var statuses = require('statuses')

      var res = Object.create(http.ServerResponse.prototype)

      module.exports = res

      /**
       * Set status \`code\`.
       */

      res.status = function status(code) {
        this.statusCode = code;
        return this;
      };

      /**
       * Send a response.
       */

      res.send = function send(body) {
        var chunk = body;
        var req = this.req;

        switch (typeof chunk) {
          case 'string':
            if (!this.get('Content-Type')) {
              this.type('html');
            }
            break;
          case 'boolean':
          case 'number':
          case 'object':
            if (chunk === null) {
              chunk = '';
            } else if (Buffer.isBuffer(chunk)) {
              if (!this.get('Content-Type')) {
                this.type('bin');
              }
            } else {
              return this.json(chunk);
            }
            break;
        }

        if (req.method === 'HEAD') {
          this.end();
        } else {
          this.end(chunk);
        }

        return this;
      };

      /**
       * Send JSON response.
       */

      res.json = function json(obj) {
        var app = this.app;
        var body = JSON.stringify(obj, app.get('json replacer'), app.get('json spaces'));

        if (!this.get('Content-Type')) {
          this.set('Content-Type', 'application/json');
        }

        return this.send(body);
      };

      /**
       * Redirect to the given \`url\` with optional response \`status\`
       * defaulting to 302.
       */

      res.redirect = function redirect(url) {
        var status = arguments.length === 2 ? arguments[0] : 302;
        var address = arguments.length === 2 ? arguments[1] : url;

        this.statusCode = status;
        this.set('Location', address);
        this.end(statuses.message[status] + '. Redirecting to ' + address);
      };
    `),
  }),
  file({
    path: 'lib/view.js',
    group: 'util',
    imports: [],
    exports: ['View', 'View.prototype.lookup', 'View.prototype.render'],
    summary: 'Resolves template files on disk and delegates rendering to the registered template engine.',
    content: code(`
      'use strict';

      var debug = require('debug')('express:view');
      var path = require('path');
      var fs = require('fs');

      var dirname = path.dirname;
      var extname = path.extname;
      var join = path.join;
      var resolve = path.resolve;

      module.exports = View;

      function View(name, options) {
        var opts = options || {};

        this.defaultEngine = opts.defaultEngine;
        this.ext = extname(name);
        this.name = name;
        this.root = opts.root;

        if (!this.ext && !this.defaultEngine) {
          throw new Error('No default engine was specified and no extension was provided.');
        }

        var fileName = name;
        this.engine = opts.engines[this.ext];
        this.path = this.lookup(fileName);
      }

      View.prototype.lookup = function lookup(name) {
        var roots = [].concat(this.root);

        for (var i = 0; i < roots.length; i++) {
          var loc = resolve(roots[i], name);
          var dir = dirname(loc);
          var file = join(dir, name);

          if (fs.existsSync(file)) return file;
        }
      };

      View.prototype.render = function render(options, callback) {
        debug('render "%s"', this.path);
        this.engine(this.path, options, callback);
      };
    `),
  }),
  file({
    path: 'lib/utils.js',
    group: 'util',
    imports: [],
    exports: ['compileETag', 'compileQueryParser', 'compileTrust', 'setCharset'],
    summary: 'Shared helpers for compiling settings (etag, query parser, trust proxy) into functions.',
    content: code(`
      'use strict';

      var contentType = require('content-type');
      var etag = require('etag');
      var proxyaddr = require('proxy-addr');
      var qs = require('qs');
      var querystring = require('querystring');

      exports.compileETag = function(val) {
        var fn;

        if (typeof val === 'function') {
          return val;
        }

        switch (val) {
          case true:
          case 'weak':
            fn = exports.wetag;
            break;
          case false:
            break;
          case 'strong':
            fn = exports.etag;
            break;
          default:
            throw new TypeError('unknown value for etag function: ' + val);
        }

        return fn;
      }

      exports.compileQueryParser = function compileQueryParser(val) {
        switch (val) {
          case true:
          case 'simple':
            return querystring.parse;
          case false:
            return undefined;
          case 'extended':
            return function parseExtendedQueryString(str) {
              return qs.parse(str, { allowPrototypes: true });
            };
          default:
            throw new TypeError('unknown value for query parser function: ' + val);
        }
      }

      exports.setCharset = function setCharset(type, charset) {
        if (!type || !charset) return type;

        var parsed = contentType.parse(type);
        parsed.parameters.charset = charset;
        return contentType.format(parsed);
      };
    `),
  }),
  file({
    path: 'examples/auth/index.js',
    imports: [],
    exports: [],
    summary: 'Example app showing session-based authentication with a restrict() middleware.',
    content: code(`
      'use strict'

      var express = require('../..');
      var hash = require('pbkdf2-password')()
      var path = require('path');
      var session = require('express-session');

      var app = module.exports = express();

      app.set('view engine', 'ejs');
      app.set('views', path.join(__dirname, 'views'));

      app.use(express.urlencoded({ extended: false }))
      app.use(session({
        resave: false, // don't save session if unmodified
        saveUninitialized: false, // don't create session until something stored
        secret: 'shhhh, very secret'
      }));

      // dummy database

      var users = {
        tj: { name: 'tj' }
      };

      // when you create a user, generate a salt
      // and hash the password ('foobar' is the pass here)

      hash({ password: 'foobar' }, function (err, pass, salt, hash) {
        if (err) throw err;
        users.tj.salt = salt;
        users.tj.hash = hash;
      });

      // Authenticate using our plain-object database

      function authenticate(name, pass, fn) {
        var user = users[name];
        if (!user) return fn(null, null)
        hash({ password: pass, salt: user.salt }, function (err, pass, salt, hash) {
          if (err) return fn(err);
          if (hash === user.hash) return fn(null, user)
          fn(null, null)
        });
      }

      function restrict(req, res, next) {
        if (req.session.user) {
          next();
        } else {
          req.session.error = 'Access denied!';
          res.redirect('/login');
        }
      }

      app.get('/restricted', restrict, function(req, res){
        res.send('Wahoo! restricted area, click to <a href="/logout">logout</a>');
      });

      app.post('/login', function (req, res, next) {
        authenticate(req.body.username, req.body.password, function(err, user){
          if (err) return next(err)
          if (user) {
            // Regenerate session when signing in to prevent fixation
            req.session.regenerate(function(){
              req.session.user = user;
              res.redirect('back');
            });
          } else {
            req.session.error = 'Authentication failed, please check your username and password.';
            res.redirect('/login');
          }
        });
      });

      if (!module.parent) {
        app.listen(3000);
        console.log('Express started on port 3000');
      }
    `),
  }),
  file({
    path: 'examples/error/index.js',
    imports: [],
    exports: [],
    summary: 'Example app demonstrating synchronous and asynchronous errors reaching an error-handling middleware.',
    content: code(`
      'use strict'

      var express = require('../../');
      var logger = require('morgan');
      var app = module.exports = express();
      var test = app.get('env') === 'test'

      if (!test) app.use(logger('dev'));

      // error handling middleware have an arity of 4
      // instead of the typical (req, res, next),
      // otherwise they behave exactly like regular
      // middleware, you may have several of them,
      // in different orders etc.

      function error(err, req, res, next) {
        // log it
        if (!test) console.error(err.stack);

        // respond with 500 "Internal Server Error".
        res.status(500);
        res.send('Internal Server Error');
      }

      app.get('/', function () {
        // Caught and passed down to the errorHandler middleware
        throw new Error('something broke!');
      });

      app.get('/next', function(req, res, next){
        // We can also pass exceptions to next()
        // The reason for process.nextTick() is to show that
        // next() can be called inside an async operation,
        // in real life it can be a DB read or HTTP request.
        process.nextTick(function(){
          next(new Error('oh no!'));
        });
      });

      // the error handler is placed after routes
      // if it were above it would not receive errors
      // from app.get() etc
      app.use(error);

      if (!module.parent) {
        app.listen(3000);
        console.log('Express started on port 3000');
      }
    `),
  }),
  file({
    path: 'examples/mvc/db.js',
    imports: [],
    exports: ['pets', 'users'],
    summary: 'In-memory fake database used by the MVC example.',
    content: code(`
      'use strict'

      // faux database

      var pets = exports.pets = [];

      pets.push({ name: 'Tobi', id: 0 });
      pets.push({ name: 'Loki', id: 1 });
      pets.push({ name: 'Jane', id: 2 });
      pets.push({ name: 'Raul', id: 3 });

      var users = exports.users = [];

      users.push({ name: 'TJ', pets: [pets[0], pets[1], pets[2]], id: 0  });
      users.push({ name: 'Guillermo', pets: [pets[3]], id: 1 });
      users.push({ name: 'Nathan', pets: [], id: 2 });
    `),
  }),
  file({
    path: 'package.json',
    content: code(`
      {
        "name": "express",
        "description": "Fast, unopinionated, minimalist web framework",
        "version": "4.21.2",
        "license": "MIT",
        "repository": "expressjs/express",
        "keywords": ["express", "framework", "web", "http", "rest", "router", "app", "api"],
        "dependencies": {
          "accepts": "~1.3.8",
          "body-parser": "1.20.3",
          "debug": "2.6.9",
          "finalhandler": "1.3.1",
          "path-to-regexp": "0.1.12",
          "qs": "6.13.0",
          "serve-static": "1.16.2"
        },
        "devDependencies": {
          "ejs": "3.1.10",
          "express-session": "1.18.1",
          "mocha": "10.7.3",
          "supertest": "6.3.0"
        },
        "engines": {
          "node": ">= 0.10.0"
        },
        "scripts": {
          "lint": "eslint .",
          "test": "mocha --require test/support/env --reporter spec --bail --check-leaks test/ test/acceptance/"
        }
      }
    `),
  }),
  file({
    path: 'Readme.md',
    content: code(`
      # Express

      Fast, unopinionated, minimalist web framework for Node.js.

      ## Installation

      This is a Node.js module available through the npm registry.

          npm install express

      ## Features

        * Robust routing
        * Focus on high performance
        * HTTP helpers (redirection, caching, etc)
        * View system supporting 14+ template engines
        * Content negotiation
        * Executable for generating applications quickly

      ## Examples

      To view the examples, clone the Express repository and install the dependencies:

          git clone https://github.com/expressjs/express.git --depth 1
          cd express
          npm install

      Then run whichever example you want:

          node examples/content-negotiation

      ## License

      MIT
    `),
  }),
]

const cite = createCiter(files)

export const expressDataset: RepositoryDataset = {
  files,
  insights: {
    summary:
      'Express is a thin layer over Node\u2019s http module. createApplication() returns a plain function that is passed to http.createServer(). Every request flows through app.handle() into a Router, which walks an ordered stack of Layers. Each Layer pairs a path matcher with a handler; route Layers delegate to a Route that dispatches by HTTP method. Errors are propagated by calling next(err), which skips regular handlers until a 4-argument error handler is found. The request and response objects are extended through prototype chaining rather than wrapping.',
    entryPoints: [
      { path: 'index.js', name: 'index.js', description: 'Package main; re-exports createApplication.' },
      { path: 'lib/express.js', name: 'createApplication()', description: 'Builds the app function and mixes in prototypes.' },
      { path: 'lib/application.js', name: 'app.listen()', description: 'Creates the HTTP server bound to the app.' },
    ],
    keyModules: [
      { path: 'lib/router/index.js', name: 'Router', description: 'Ordered middleware stack and the next() loop.' },
      { path: 'lib/router/layer.js', name: 'Layer', description: 'Path matching and arity-based handler dispatch.' },
      { path: 'lib/router/route.js', name: 'Route', description: 'Per-method handler stacks for a single path.' },
      { path: 'lib/response.js', name: 'response', description: 'res.send(), res.json() and other HTTP helpers.' },
    ],
  },
  answers: [
    {
      keywords: ['auth', 'login', 'session', 'password'],
      content: `Express core **does not ship an authentication layer**. Authentication is implemented in userland as ordinary middleware, and the repository demonstrates the recommended pattern in \`examples/auth\`.

### How the example works

1. **Sessions** are enabled with \`express-session\`, so \`req.session\` persists between requests.
2. **Passwords** are hashed with \`pbkdf2-password\` using a per-user salt.
3. A \`restrict\` middleware guards protected routes by checking \`req.session.user\`:

~~~js
function restrict(req, res, next) {
  if (req.session.user) {
    next();
  } else {
    req.session.error = 'Access denied!';
    res.redirect('/login');
  }
}

app.get('/restricted', restrict, handler);
~~~

4. On successful login the session is **regenerated** before storing the user, which prevents session fixation attacks.

Because \`restrict\` is just a function with the \`(req, res, next)\` signature, it plugs into the same Layer dispatch used by every other handler.`,
      citations: [
        cite('examples/auth/index.js', 'function restrict', 9),
        cite('examples/auth/index.js', "app.post('/login'", 16),
        cite('lib/router/layer.js', 'Layer.prototype.handle_request', 15),
      ],
    },
    {
      keywords: ['entry', 'api', 'start', 'listen', 'main', 'bootstrap'],
      content: `The public API entry point is \`index.js\`, which simply re-exports \`lib/express.js\`.

### Startup sequence

- **\`createApplication()\`** in \`lib/express.js\` creates \`app\` as a function \`(req, res, next) => app.handle(...)\`, then mixes in \`EventEmitter\` and the application prototype.
- **\`app.init()\`** sets default configuration such as \`env\`, \`etag\` and \`query parser\`.
- **\`app.listen()\`** passes the app function to \`http.createServer\`:

~~~js
app.listen = function listen() {
  var server = http.createServer(this);
  return server.listen.apply(server, arguments);
};
~~~

From there, every incoming request enters \`app.handle()\`, which forwards it to the lazily created base router.`,
      citations: [
        cite('index.js', "module.exports = require('./lib/express')", 1),
        cite('lib/express.js', 'function createApplication()', 22),
        cite('lib/application.js', 'app.listen = function listen', 4),
        cite('lib/application.js', 'app.handle = function handle', 20),
      ],
    },
    {
      keywords: ['database', 'db', 'schema', 'model', 'storage', 'sql'],
      content: `Express is **database-agnostic** and the core library has no database layer, models, or schema. Persistence is left to the application.

The only data structures in the repository are **in-memory fixtures** used by examples. For instance, \`examples/mvc/db.js\` exposes two arrays:

| Collection | Shape |
| --- | --- |
| \`pets\` | \`{ id, name }\` |
| \`users\` | \`{ id, name, pets: Pet[] }\` |

Similarly, \`examples/auth/index.js\` uses a plain object keyed by username as a "dummy database".

If you are integrating a real database, the idiomatic approach is to open a connection at startup and query it inside route handlers, forwarding failures with \`next(err)\`.`,
      citations: [
        cite('examples/mvc/db.js', 'var pets = exports.pets', 12),
        cite('examples/auth/index.js', '// dummy database', 5),
      ],
    },
    {
      keywords: ['error', 'exception', 'throw', 'fail', '500'],
      content: `Error handling is driven by the \`next()\` loop in the Router and by **handler arity**.

### Propagation

When a handler throws or calls \`next(err)\`, the router keeps walking the stack but switches dispatch mode:

~~~js
if (layerError) {
  layer.handle_error(layerError, req, res, next);
} else {
  layer.handle_request(req, res, next);
}
~~~

### Arity check

\`Layer#handle_error\` only invokes functions declared with **four arguments** \`(err, req, res, next)\`. Regular middleware are skipped. Synchronous throws are caught with \`try/catch\` and converted into \`next(err)\`.

### Final handler

If no error handler responds, \`app.handle()\` falls back to \`finalhandler\`, which sends an appropriate status and logs the stack via \`logerror\` (outside of the \`test\` environment).

See \`examples/error/index.js\` for a working example that registers an error handler **after** all routes.`,
      citations: [
        cite('lib/router/index.js', 'req.params = layer.params;', 8),
        cite('lib/router/layer.js', 'Layer.prototype.handle_error', 16),
        cite('lib/application.js', 'var done = callback || finalhandler', 5),
        cite('examples/error/index.js', 'function error(err, req, res, next)', 8),
      ],
    },
    {
      keywords: ['route', 'routing', 'router', 'middleware', 'next'],
      content: `Routing is built from two primitives:

- **\`Layer\`** pairs a compiled path regexp with a handler.
- **\`Route\`** groups handlers for one path, keyed by HTTP method.

\`app.get('/users', fn)\` calls \`router.route('/users')\`, which pushes a Layer whose handler is \`route.dispatch\`. \`app.use(fn)\` pushes a Layer with \`end: false\`, so it matches any path prefix.

At request time \`Router#handle\` iterates the stack, calling \`layer.match(path)\` until it finds a match, then dispatches. Each handler advances the chain by calling \`next()\`.`,
      citations: [
        cite('lib/router/index.js', 'proto.route = function route', 14),
        cite('lib/router/route.js', 'Route.prototype.dispatch', 20),
        cite('lib/router/layer.js', 'Layer.prototype.match', 14),
      ],
    },
  ],
  fallbackAnswer: {
    content: `I couldn't find a section of the indexed source that answers this precisely, so here is the most relevant context.

Express is organised around a small core:

- \`lib/express.js\` creates the application function.
- \`lib/application.js\` holds settings, mounting, and \`app.handle()\`.
- \`lib/router/*\` implements the middleware stack.
- \`lib/request.js\` and \`lib/response.js\` extend Node's HTTP objects.

Try asking about **routing**, **error handling**, or the **API entry point** for a more specific answer.`,
    citations: [cite('lib/express.js', 'function createApplication()', 10), cite('lib/application.js', 'app.handle = function handle', 10)],
  },
}
