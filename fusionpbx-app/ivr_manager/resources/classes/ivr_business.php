<?php
/**
 * Business profile / prompt templating for the FusionPBX app — parity with the
 * Python app's business.py.
 *
 * A business name plus arbitrary named templates (reusable text snippets) become
 * {placeholders} that can be dropped into a greeting's TTS text and are resolved
 * (recursively, with a cycle guard) before synthesis. Stored in the app's own
 * settings table via ivr_settings; not secret, but kept out of Default Settings.
 */
class ivr_business {

	const MAX_DEPTH = 10;

	private $settings;      // ivr_settings
	private $domain_name;

	public function __construct($settings, $domain_name) {
		$this->settings = $settings;
		$this->domain_name = $domain_name;
	}

	public function business_name() {
		$v = trim((string) $this->settings->get('business_name', ''));
		return $v !== '' ? $v : $this->domain_name;
	}

	/** Named templates as an associative array (name => value). */
	public function templates() {
		$raw = $this->settings->get('templates', '');
		if ($raw === '' || $raw === null) {
			return array();
		}
		$t = json_decode($raw, true);
		return is_array($t) ? $t : array();
	}

	public function closure_opening() {
		return trim((string) $this->settings->get('closure_opening', ''));
	}

	public function closure_closing() {
		return trim((string) $this->settings->get('closure_closing', ''));
	}

	/** Placeholder key => value (business_name is a reserved built-in). */
	public function placeholders() {
		$ph = $this->templates();
		$ph['business_name'] = $this->business_name();
		return $ph;
	}

	/** ['{business_name}', '{greeting}', ...] for UI hints. */
	public function placeholder_keys() {
		$keys = array();
		foreach (array_keys($this->placeholders()) as $k) {
			$keys[] = '{' . $k . '}';
		}
		return $keys;
	}

	/**
	 * Substitute {placeholders}, resolving nested templates recursively; unknown
	 * placeholders (and cycles) are left untouched.
	 */
	public function render($text) {
		if ($text === '' || $text === null) {
			return (string) $text;
		}
		return $this->render_inner($text, $this->placeholders(), array(), 0);
	}

	private function render_inner($text, $ph, $seen, $depth) {
		if ($text === '' || $text === null || $depth > self::MAX_DEPTH) {
			return (string) $text;
		}
		$self = $this;
		return preg_replace_callback('/\{([a-zA-Z0-9_.-]+)\}/', function ($m) use ($ph, $seen, $depth, $self) {
			$key = $m[1];
			if (!isset($ph[$key]) || in_array($key, $seen, true)) {
				return $m[0];   // unknown or cycle -> leave as-is
			}
			return $self->render_public($ph[$key], $ph, array_merge($seen, array($key)), $depth + 1);
		}, $text);
	}

	// exposed so the closure can recurse (PHP 7.0 has no arrow-fn $this binding)
	public function render_public($text, $ph, $seen, $depth) {
		return $this->render_inner($text, $ph, $seen, $depth);
	}
}
