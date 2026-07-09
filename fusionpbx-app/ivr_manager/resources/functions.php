<?php
/**
 * Cross-version FusionPBX compatibility shims (4.5.x → 5.x).
 *
 * Some framework helpers exist only in newer FusionPBX (e.g. the `button` class
 * arrived in 5.x) or vary in signature. These wrappers feature-detect and fall
 * back to plain HTML/stdlib so the app doesn't fatal on a 4.5.x host.
 */

if (!function_exists('ivrmgr_esc')) {
	function ivrmgr_esc($string) {
		if (function_exists('escape')) {
			return escape($string);
		}
		return htmlspecialchars((string) $string, ENT_QUOTES);
	}
}

if (!function_exists('ivrmgr_button')) {
	/**
	 * Render a button. Uses FusionPBX's button::create when present (5.x), else
	 * emits equivalent plain HTML that works on 4.5.x.
	 * $opts: type(button|submit), label, title, icon, link, onclick, name, value
	 */
	function ivrmgr_button($opts) {
		if (class_exists('button') && method_exists('button', 'create')) {
			return button::create($opts);
		}
		// Fallback (older FusionPBX without the button class): render a real
		// <button> with FusionPBX's button classes so it looks native, honoring
		// name/value for submit buttons.
		$type = isset($opts['type']) ? $opts['type'] : 'button';
		$label = isset($opts['label']) ? $opts['label'] : (isset($opts['title']) ? $opts['title'] : '');
		$onclick = isset($opts['onclick']) ? $opts['onclick'] : '';
		$cls = "class='btn btn-default button'";
		$e = function ($v) { return htmlspecialchars($v, ENT_QUOTES); };
		$name_attr = !empty($opts['name']) ? " name='" . $e($opts['name']) . "'" : "";
		$value_attr = isset($opts['value']) ? " value='" . $e($opts['value']) . "'" : "";
		if ($type === 'submit') {
			$oc = $onclick !== '' ? " onclick=\"" . $e($onclick) . "\"" : "";
			return "<button type='submit' $cls$name_attr$value_attr$oc>" . $e($label) . "</button>";
		}
		if (!empty($opts['link'])) {
			$nav = "window.location.href='" . $e($opts['link']) . "';";
			$guard = ($onclick !== '') ? "if(" . $onclick . "){" . $nav . "}" : $nav;
			return "<button type='button' $cls onclick=\"" . $e($guard) . "\">" . $e($label) . "</button>";
		}
		$oc = $onclick !== '' ? " onclick=\"" . $e($onclick) . "\"" : "";
		return "<button type='button' $cls$oc>" . $e($label) . "</button>";
	}
}

if (!function_exists('ivrmgr_message')) {
	/** Flash a message. Uses message::add on 5.x, else a session flash we render. */
	function ivrmgr_message($text, $type = 'positive') {
		if (class_exists('message') && method_exists('message', 'add')) {
			message::add($text, $type);
			return;
		}
		if (!isset($_SESSION['ivrmgr_flash']) || !is_array($_SESSION['ivrmgr_flash'])) {
			$_SESSION['ivrmgr_flash'] = array();
		}
		$_SESSION['ivrmgr_flash'][] = array('text' => $text, 'type' => $type);
	}
}

if (!function_exists('ivrmgr_render_flash')) {
	/** Render + clear any session flash (only needed on the 4.5.x fallback path). */
	function ivrmgr_render_flash() {
		if (empty($_SESSION['ivrmgr_flash'])) {
			return;
		}
		foreach ($_SESSION['ivrmgr_flash'] as $m) {
			$color = ($m['type'] === 'negative') ? '#a94442' : '#3c763d';
			echo "<div style='padding:.5em .8em; margin:.4em 0; border-left:4px solid " . $color . ";'>"
				. htmlspecialchars($m['text'], ENT_QUOTES) . "</div>\n";
		}
		unset($_SESSION['ivrmgr_flash']);
	}
}

if (!function_exists('ivrmgr_pdo')) {
	/**
	 * Return a connected PDO from the FusionPBX database class. The `database`
	 * object does NOT populate its PDO until connect() is called (and the property
	 * name has varied), so this connects and finds the handle defensively.
	 */
	function ivrmgr_pdo() {
		$database = new database;
		if (method_exists($database, 'connect')) {
			$database->connect();
		}
		foreach (array('db', 'pdo', 'conn') as $p) {
			if (isset($database->$p) && $database->$p instanceof PDO) {
				return $database->$p;
			}
		}
		throw new Exception('could not obtain a database connection from FusionPBX');
	}
}

if (!function_exists('ivrmgr_reloadxml')) {
	/** Ask FreeSWITCH to reloadxml after a change (best-effort). */
	function ivrmgr_reloadxml() {
		if (!class_exists('event_socket')) {
			return;
		}
		try {
			// Preferred: FusionPBX's static API connects using the configured
			// event-socket settings itself (works on 4.x and 5.x).
			if (method_exists('event_socket', 'create')) {
				$fp = event_socket::create();
				if ($fp) {
					if (method_exists('event_socket', 'command')) {
						event_socket::command('reloadxml');
					} elseif (method_exists('event_socket', 'api')) {
						event_socket::api('reloadxml');
					}
				}
				return;
			}
			// Fallback: instance connect. 5.x's connect() needs (host,port,pass);
			// older signatures ignore the extra args, so passing 3 is safe on both.
			$esl = new event_socket;
			$es = isset($_SESSION['event_socket']) ? $_SESSION['event_socket'] : array();
			$host = isset($es['host']['text']) ? $es['host']['text'] : '127.0.0.1';
			$port = isset($es['port']['text']) ? $es['port']['text'] : '8021';
			$pass = isset($es['password']['text']) ? $es['password']['text'] : 'ClueCon';
			if ($esl->connect($host, $port, $pass) && method_exists($esl, 'request')) {
				$esl->request('api reloadxml');
			}
		} catch (\Throwable $e) {
			// ignore — the admin can Reload XML from the GUI
		}
	}
}
