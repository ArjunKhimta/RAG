# Candidate evaluation questions for pallets/flask 3.1.3

Real questions from Stack Overflow, gathered on 2026-10-03 through its public read-only API, for the
38 open slots in `pallets-flask-3.1.3.json` (12 of 50 written when this list was made). Each answer location was checked
against the Flask 3.1.3 source.

Question titles are from Stack Overflow, written by their original authors and licensed under
CC BY-SA (https://stackoverflow.com/help/licensing); each one links to its source.

## How to use this list

1. Pick a question and rewrite it in your own words. Stack Overflow text is licensed (CC BY-SA), so
   only titles and links are kept here, and the questions in the evaluation file must be your own
   wording.
2. Find the expected definitions by reading Flask 3.1.3's code, not the old accepted answer: many
   questions predate changes in Flask.
3. Add it to `pallets-flask-3.1.3.json` with `origin` set to `stack_overflow` and `source_url` set to
   the link. Both fields are still to be added to the file format.
4. Tick the box and note the new question ID, for example `-> flask-013`.

Key: 🔤 good as a name question (type just the function name) · ⚠️ part of the answer is in Werkzeug
or Jinja, which are not indexed · 📅 old question, and Flask has changed since

Goals for the full set of 50: about 10 name questions (2 so far), a few more questions needing two
connected definitions (4 so far), and 2 or 3 questions the code cannot answer (none so far).

## Routing and endpoints

- [x] 1. [Method Not Allowed flask error 405](https://stackoverflow.com/q/21689364): `App.add_url_rule` (routes accept only GET unless methods are given) -> flask-013
- [ ] 2. [What is an 'endpoint' in Flask?](https://stackoverflow.com/q/19261833): `App.add_url_rule`, `_endpoint_from_view_func`
- [ ] 3. [View function mapping is overwriting an existing endpoint function](https://stackoverflow.com/q/17256602): `App.add_url_rule` (raises that error)
- [ ] 4. [before_request: add exception for specific route](https://stackoverflow.com/q/14367991): `Scaffold.before_request`, `Flask.preprocess_request`
- [x] 5. [How to serve static files in Flask](https://stackoverflow.com/q/20646822): `Flask.__init__` (adds the static route), `Flask.send_static_file` -> flask-033

## Building URLs

- [ ] 6. [Create dynamic URLs in Flask with url_for()](https://stackoverflow.com/q/7478366) 🔤: `Flask.url_for`
- [ ] 7. [Where do I define the domain to be used by url_for()?](https://stackoverflow.com/q/12162634): `Flask.create_url_adapter` (`SERVER_NAME`)
- [ ] 8. [url_for generating http URL instead of https](https://stackoverflow.com/q/14810795) ⚠️: `Flask.url_for` (`_scheme`, `PREFERRED_URL_SCHEME`); behind a proxy the fix is in Werkzeug
- [x] 9. [url_for() error: without the application context being pushed](https://stackoverflow.com/q/31766082): `Flask.url_for` (raises "Unable to build URLs outside an active request...") -> flask-025

## Blueprints

- [ ] 10. [What are Flask Blueprints, exactly?](https://stackoverflow.com/q/24420857): `Blueprint`, `Blueprint.register` (both in `src/flask/sansio/blueprints.py`)
- [ ] 11. [How to access app.config in a blueprint?](https://stackoverflow.com/q/18214612): `Blueprint.record`, `BlueprintSetupState`
- [ ] 12. [Flask blueprint template folder](https://stackoverflow.com/q/7974771): `DispatchingJinjaLoader._iter_loaders` (the app's templates win over a blueprint's)
- [x] 13. [errorhandler in separate blueprint is not working](https://stackoverflow.com/q/55785287): `Blueprint.app_errorhandler`, `App._find_error_handler` -> flask-016
- [x] 14. [Nested Blueprints in Flask?](https://stackoverflow.com/q/33003178): `Blueprint.register_blueprint`, `Blueprint.register` -> flask-029

## Contexts and `g`

- [x] 15. [When should Flask.g be used?](https://stackoverflow.com/q/15083967): `_AppCtxGlobals`, `AppContext` -> flask-014
- [ ] 16. [Flask: 'session' vs. 'g'?](https://stackoverflow.com/q/32909851): `_AppCtxGlobals`, `SecureCookieSession` (two-part)
- [ ] 17. [RuntimeError: working outside of application context](https://stackoverflow.com/q/31444036): `src/flask/globals.py`, where the message is module-level text, not inside a function; decide how to mark it as expected before using it
- [ ] 18. [Testing code that requires a Flask app or request context](https://stackoverflow.com/q/17375340): `Flask.app_context`, `Flask.test_request_context`
- [x] 19. ["Working outside of request context" in a background thread](https://stackoverflow.com/q/31647081) 🔤: `copy_current_request_context` -> flask-027
- [ ] 20. [Access the request in after_request or teardown_request](https://stackoverflow.com/q/27938818): `Flask.do_teardown_request`, `RequestContext.pop`

## Hooks around each request

- [ ] 21. [How to set response header for all responses](https://stackoverflow.com/q/30717152): `Scaffold.after_request`, `Flask.process_response`
- [x] 22. [How to run code after send_file()](https://stackoverflow.com/q/29192132) 🔤: `after_this_request` -> flask-019

## Everyday helpers

- [x] 23. [Redirecting to URL in Flask](https://stackoverflow.com/q/14343812) 🔤: `redirect`, `App.redirect` -> flask-034
- [ ] 24. [How to return 400 (Bad Request) on Flask?](https://stackoverflow.com/q/57664997): `abort`, `App.make_aborter`
- [x] 25. [Difference between send_file and send_from_directory?](https://stackoverflow.com/q/38252955): `send_file`, `send_from_directory` (two-part) -> flask-017
- [ ] 26. [How to change downloading name in Flask?](https://stackoverflow.com/q/41543951): `send_file` (`download_name`)
- [x] 27. [Streaming data with Python and Flask](https://stackoverflow.com/q/13386681) 🔤: `stream_with_context`, `stream_template` -> flask-030
- [x] 28. [Message flashing fails across redirects](https://stackoverflow.com/q/6196598): `flash`, `get_flashed_messages` (messages are kept in the session) -> flask-015
- [ ] 29. [Flash success and danger with different messages](https://stackoverflow.com/q/51273822): `flash`, `get_flashed_messages` (categories)

## Templates

- [ ] 30. [TemplateNotFound even though template file exists](https://stackoverflow.com/q/23327293): `DispatchingJinjaLoader.get_source`, `explain_template_loading_attempts`
- [x] 31. [Reload Flask app when template file changes](https://stackoverflow.com/q/9508667): `Flask.create_jinja_environment` (`TEMPLATES_AUTO_RELOAD`) -> flask-021
- [ ] 32. [Flask context processors functions](https://stackoverflow.com/q/13809890): `Scaffold.context_processor`, `Flask.update_template_context`

## JSON

- [x] 33. [How do I jsonify a list in Flask?](https://stackoverflow.com/q/12435297) 🔤: `jsonify`, `JSONProvider._prepare_response_obj` -> flask-035
- [x] 34. [Object is not JSON serializable](https://stackoverflow.com/q/11280382): `_default` in `src/flask/json/provider.py` (the types Flask converts for you) -> flask-022
- [ ] 35. [Keep order of sorted dictionary passed to jsonify()](https://stackoverflow.com/q/54446080) 📅: `DefaultJSONProvider.sort_keys` (the old setting was removed)

## Upload size limits

- [x] 36. [Limit POST data size on a per-route basis?](https://stackoverflow.com/q/25036498) 📅⚠️: `Request.max_content_length` (settable per request since Flask 3.1) -> flask-032

## Command line

- [ ] 37. [Where should I implement custom commands?](https://stackoverflow.com/q/57202736): `AppGroup.command`
- [x] 38. [How to access app context in a CLI command](https://stackoverflow.com/q/51822129) 🔤: `with_appcontext`, `AppGroup.command` -> flask-023
- [ ] 39. [Run Flask dev server over HTTPS using CLI](https://stackoverflow.com/q/48467835): `CertParamType`, `_validate_key`, `run_command`
- [ ] 40. [Change the host and port that the flask command uses](https://stackoverflow.com/q/41940663): `run_command`, `FlaskGroup.__init__` (`FLASK_` environment variables)
- [ ] 41. [.flaskenv or .env file not being read](https://stackoverflow.com/q/62411746): `load_dotenv` (needs python-dotenv installed)
- [ ] 42. [Invoke a Flask CLI command programmatically?](https://stackoverflow.com/q/50963130): `FlaskCliRunner.invoke`, `Flask.test_cli_runner`

## Config, sessions, views, testing, async

- [x] 43. [Using config classes with from_object() for dev/prod/testing](https://stackoverflow.com/q/61622845) 🔤: `Config.from_object` (only UPPERCASE names load) -> flask-024
- [ ] 44. [Flask permanent session: where to define them?](https://stackoverflow.com/q/34118093): `SessionMixin.permanent`, `SessionInterface.get_expiration_time`
- [x] 45. [About as_view function in Flask](https://stackoverflow.com/q/15098215) 🔤: `View.as_view` -> flask-018
- [x] 46. [Unit test a Flask session (session_transaction)](https://stackoverflow.com/q/16528679) 🔤: `FlaskClient.session_transaction` -> flask-026
- [x] 47. [Install Flask with the 'async' extra to use async views](https://stackoverflow.com/q/70321014): `Flask.async_to_sync`, `Flask.ensure_sync` -> flask-031
- [ ] 48. [Connect to a database in Flask: which approach is better?](https://stackoverflow.com/q/16311974): not in Flask itself; the tutorial example shows how, in `get_db` and `close_db` in `examples/tutorial/flaskr/db.py`

## Not in the code (the right reply is "the code does not show this")

- [x] 49. [ImportError: cannot import name 'json' from itsdangerous](https://stackoverflow.com/q/71189819): a package-version mismatch; Flask 3.1.3 does not contain the old import -> flask-020
- [x] 50. [ImportError: cannot import name 'url_quote' from werkzeug.urls](https://stackoverflow.com/q/77213053): the same kind of version problem -> flask-028
- [x] 51. [Flask CLI throws 'Exec format error' through docker-compose](https://stackoverflow.com/q/55271912): a Docker setup problem, not Flask code -> flask-036
