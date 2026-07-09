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
		$type = isset($opts['type']) ? $opts['type'] : 'button';
		$label = isset($opts['label']) ? $opts['label'] : (isset($opts['title']) ? $opts['title'] : '');
		$onclick = isset($opts['onclick']) ? $opts['onclick'] : '';
		if ($type === 'submit') {
			return "<input type='submit' class='btn' value='" . htmlspecialchars($label, ENT_QUOTES) . "'"
				. ($onclick !== '' ? " onclick=\"" . htmlspecialchars($onclick, ENT_QUOTES) . "\"" : "") . ">";
		}
		if (!empty($opts['link'])) {
			$nav = "window.location.href='" . htmlspecialchars($opts['link'], ENT_QUOTES) . "';";
			$guard = ($onclick !== '') ? "if(" . $onclick . "){" . $nav . "}" : $nav;
			return "<input type='button' class='btn' value='" . htmlspecialchars($label, ENT_QUOTES) . "' onclick=\"" . htmlspecialchars($guard, ENT_QUOTES) . "\">";
		}
		return "<input type='button' class='btn' value='" . htmlspecialchars($label, ENT_QUOTES) . "'"
			. ($onclick !== '' ? " onclick=\"" . htmlspecialchars($onclick, ENT_QUOTES) . "\"" : "") . ">";
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

if (!function_exists('ivrmgr_reloadxml')) {
	/** Ask FreeSWITCH to reloadxml after a change (best-effort). */
	function ivrmgr_reloadxml() {
		if (class_exists('event_socket')) {
			try {
				$esl = new event_socket;
				if (method_exists($esl, 'connect') && $esl->connect()) {
					$esl->request('api reloadxml');
				}
			} catch (Exception $e) {
				// ignore — the admin can Reload XML from the GUI
			}
		}
	}
}
