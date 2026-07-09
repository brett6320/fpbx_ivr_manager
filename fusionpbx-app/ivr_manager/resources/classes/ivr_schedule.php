<?php
/**
 * IVR Manager — time-condition (schedule) engine for the FusionPBX app variant.
 *
 * Mirrors the Python app's model: one managed time condition per extension can
 * hold several closures, emitted most-specific-first, each breaking on a match;
 * a trailing condition transfers to the open (daytime) destination when none
 * match. Ownership is proven by a marker comment before any write, so the app
 * never touches a dialplan a human/other app created.
 *
 * Compatible with FusionPBX 4.x → 5.x and PHP 7.0+ (no 7.1+-only syntax):
 * writes go straight to v_dialplans using only columns that exist on the running
 * schema (see save()), so column drift across versions is tolerated.
 */
class ivr_schedule {

	// Ownership marker embedded in every dialplan we create.
	const MARKER = 'fpbx-ivr-manager:managed';

	// FusionPBX's fixed Time Conditions app_uuid — stamping it makes our records
	// appear as native Time Conditions in the GUI (stable across 4.x/5.x).
	const TIME_CONDITIONS_APP_UUID = '4b821450-926b-175a-af93-a03c441818b1';

	const FS_DT = 'Y-m-d H:i:s';

	private $pdo;
	private $domain_uuid;
	private $domain_name;

	public function __construct($pdo, $domain_uuid, $domain_name) {
		$this->pdo = $pdo;
		$this->domain_uuid = $domain_uuid;
		$this->domain_name = $domain_name;
	}

	private function marker_xml() {
		return '<!-- ' . self::MARKER . ' -->';
	}

	private function is_managed($xml) {
		return is_string($xml) && strpos($xml, $this->marker_xml()) !== false;
	}

	private function comment_safe($text) {
		$text = (string) $text;
		$text = str_replace(array('"', '<', '>'), array("'", '', ''), $text);
		return str_replace('--', '-', $text);
	}

	private function xml_escape($text) {
		return htmlspecialchars((string) $text, ENT_QUOTES);
	}

	private function playback_path($filename) {
		// Use FreeSWITCH's $${recordings} variable + per-domain path, so we don't
		// depend on any host-specific recordings dir or session key (portable
		// across FusionPBX 4.x/5.x and any storage layout).
		return '$${recordings}/' . $this->domain_name . '/' . $filename;
	}

	private function duration_seconds($closure) {
		return strtotime($closure['end']) - strtotime($closure['start']);
	}

	/**
	 * Most specific first: shortest window wins, ties by earliest start — so a
	 * narrow closure shadows a broader one that overlaps it.
	 */
	public function sort_closures($closures) {
		usort($closures, function ($a, $b) {
			$da = strtotime($a['end']) - strtotime($a['start']);
			$db = strtotime($b['end']) - strtotime($b['start']);
			if ($da !== $db) {
				return $da < $db ? -1 : 1;
			}
			$sa = strtotime($a['start']);
			$sb = strtotime($b['start']);
			return $sa === $sb ? 0 : ($sa < $sb ? -1 : 1);
		});
		return $closures;
	}

	private function closure_block($extension, $closure) {
		$dt = $closure['start'] . '~' . $closure['end'];
		$play = $this->xml_escape($this->playback_path($closure['recording_filename']));
		if ($closure['closed_action'] === 'hangup') {
			$closed = '    <action application="hangup" data="NORMAL_CLEARING"/>';
		} else {
			$closed = '    <action application="voicemail" data="default '
				. $this->xml_escape($this->domain_name) . ' ' . $extension . '"/>';
		}
		$meta = '  <!-- ivrmgr:closure label="' . $this->comment_safe($closure['label'])
			. '" reason="' . $this->comment_safe(isset($closure['reason']) ? $closure['reason'] : '')
			. '" action="' . $this->comment_safe($closure['closed_action']) . '" -->';
		return $meta . "\n"
			. '  <condition date-time="' . $dt . '" break="on-true">' . "\n"
			. '    <action application="answer"/>' . "\n"
			. '    <action application="sleep" data="700"/>' . "\n"
			. '    <action application="playback" data="' . $play . '"/>' . "\n"
			. $closed . "\n"
			. '  </condition>';
	}

	public function build_dialplan_xml($extension, $closures, $open_destination) {
		$blocks = array();
		foreach ($this->sort_closures($closures) as $closure) {
			$blocks[] = $this->closure_block($extension, $closure);
		}
		$domain = $this->xml_escape($this->domain_name);
		return $this->marker_xml() . "\n"
			. '<extension name="schedule_' . $extension . '" continue="false">' . "\n"
			. '  <condition field="destination_number" expression="^' . $extension . '$" break="on-false"/>' . "\n"
			. implode("\n", $blocks) . "\n"
			. '  <condition field="destination_number" expression="^' . $extension . '$">' . "\n"
			. '    <action application="transfer" data="' . $this->xml_escape($open_destination) . ' XML ' . $domain . '"/>' . "\n"
			. '  </condition>' . "\n"
			. '</extension>';
	}

	/**
	 * Recover closures + open destination from stored XML. Handles both the
	 * current multi-closure format and the earlier single-condition format.
	 */
	public function parse_closures($xml) {
		$xml = (string) $xml;
		$metas = array();
		preg_match_all('/<!-- ivrmgr:closure label="([^"]*)" reason="([^"]*)" action="([^"]*)" -->/', $xml, $metas, PREG_SET_ORDER);
		$blocks = array();
		preg_match_all('/<condition date-time="([^"~]+)~([^"]+)"[^>]*>(.*?)<\/condition>/s', $xml, $blocks, PREG_SET_ORDER);
		$closures = array();
		foreach ($blocks as $i => $b) {
			$inner = $b[3];
			$meta = isset($metas[$i]) ? $metas[$i] : null;
			$rec = '';
			if (preg_match('/playback" data="[^"]*\/([^"\/]+)"/', $inner, $m)) {
				$rec = $m[1];
			}
			$action = $meta ? $meta[3] : (strpos($inner, 'voicemail') !== false ? 'voicemail' : 'hangup');
			$closures[] = array(
				'label' => ($meta && $meta[1] !== '') ? $meta[1] : 'closure',
				'reason' => $meta ? $meta[2] : '',
				'closed_action' => $action,
				'start' => $b[1],
				'end' => $b[2],
				'recording_filename' => $rec,
			);
		}
		$dest = null;
		if (preg_match_all('/transfer" data="([^ ]+) XML/', $xml, $t, PREG_SET_ORDER)) {
			$last = end($t);
			$dest = htmlspecialchars_decode($last[1], ENT_QUOTES);
		}
		return array('closures' => $closures, 'open_destination' => $dest);
	}

	// ---- persistence (schema-resilient direct writes to v_dialplans) ----

	private function existing_columns($table) {
		$sql = "select column_name from information_schema.columns "
			. "where table_schema = 'public' and table_name = :t";
		$stmt = $this->pdo->prepare($sql);
		$stmt->execute(array(':t' => $table));
		$cols = array();
		foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $r) {
			$cols[$r['column_name']] = true;
		}
		return $cols;
	}

	/** Insert/replace a row using only columns that exist on this schema. */
	private function save($table, $values) {
		$cols = $this->existing_columns($table);
		$pk = substr($table, 2) . '_uuid'; // v_dialplans -> dialplan_uuid
		$data = array();
		foreach ($values as $k => $v) {
			if (isset($cols[$k])) {
				$data[$k] = $v;
			}
		}
		$exists = false;
		if (isset($data[$pk])) {
			$stmt = $this->pdo->prepare("select 1 from $table where $pk = :id");
			$stmt->execute(array(':id' => $data[$pk]));
			$exists = (bool) $stmt->fetchColumn();
		}
		if ($exists) {
			$sets = array();
			foreach (array_keys($data) as $k) {
				if ($k === $pk) { continue; }
				$sets[] = "$k = :$k";
			}
			$sql = "update $table set " . implode(', ', $sets) . " where $pk = :$pk";
		} else {
			$keys = array_keys($data);
			$sql = "insert into $table (" . implode(', ', $keys) . ") values (:" . implode(', :', $keys) . ")";
		}
		$params = array();
		foreach ($data as $k => $v) {
			$params[':' . $k] = $v;
		}
		$this->pdo->prepare($sql)->execute($params);
	}

	private function find_managed($extension) {
		$stmt = $this->pdo->prepare(
			"select dialplan_uuid, dialplan_name, dialplan_xml from v_dialplans "
			. "where domain_uuid = :d and dialplan_number = :n");
		$stmt->execute(array(':d' => $this->domain_uuid, ':n' => (string) $extension));
		foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $r) {
			if ($this->is_managed($r['dialplan_xml'])) {
				return $r;
			}
		}
		return null;
	}

	/** Refuse if any FOREIGN dialplan already occupies the number. */
	private function assert_free_or_owned($extension) {
		$stmt = $this->pdo->prepare(
			"select dialplan_name, dialplan_xml from v_dialplans "
			. "where domain_uuid = :d and dialplan_number = :n");
		$stmt->execute(array(':d' => $this->domain_uuid, ':n' => (string) $extension));
		foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $r) {
			if (!$this->is_managed($r['dialplan_xml'])) {
				throw new Exception('extension ' . $extension . ' is used by a dialplan this app did not create');
			}
		}
	}

	/** Create/replace the time condition with the given closures. Returns uuid. */
	public function upsert($extension, $name, $closures, $open_destination) {
		$xml = $this->build_dialplan_xml($extension, $closures, $open_destination);
		$existing = $this->find_managed($extension);
		if ($existing) {
			$uuid = $existing['dialplan_uuid'];
		} else {
			$this->assert_free_or_owned($extension);
			$uuid = self::uuid();
		}
		$this->save('v_dialplans', array(
			'dialplan_uuid' => $uuid,
			'app_uuid' => self::TIME_CONDITIONS_APP_UUID,
			'domain_uuid' => $this->domain_uuid,
			'dialplan_context' => $this->domain_name,
			'dialplan_name' => 'schedule_' . $extension,
			'dialplan_number' => (string) $extension,
			'dialplan_order' => 300,
			'dialplan_enabled' => 'true',
			'dialplan_xml' => $xml,
			'dialplan_description' => $name,
		));
		return $uuid;
	}

	/** The stored dialplan XML for a managed schedule, or null. */
	public function get_xml($extension) {
		$existing = $this->find_managed($extension);
		return $existing ? $existing['dialplan_xml'] : null;
	}

	public function delete_schedule($extension) {
		$existing = $this->find_managed($extension);
		if (!$existing) {
			$this->assert_free_or_owned($extension);
			return false;
		}
		$stmt = $this->pdo->prepare("delete from v_dialplans where dialplan_uuid = :id");
		$stmt->execute(array(':id' => $existing['dialplan_uuid']));
		return true;
	}

	public function list_schedules() {
		$stmt = $this->pdo->prepare(
			"select dialplan_number, dialplan_name, dialplan_description, dialplan_enabled, dialplan_xml "
			. "from v_dialplans where domain_uuid = :d and app_uuid = :a order by dialplan_number");
		$stmt->execute(array(':d' => $this->domain_uuid, ':a' => self::TIME_CONDITIONS_APP_UUID));
		$out = array();
		foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
			if (!$this->is_managed($row['dialplan_xml'])) {
				continue;
			}
			$parsed = $this->parse_closures($row['dialplan_xml']);
			$out[] = array(
				'extension' => (int) $row['dialplan_number'],
				'label' => $row['dialplan_description'] !== '' ? $row['dialplan_description'] : $row['dialplan_name'],
				'enabled' => $row['dialplan_enabled'] === 'true',
				'open_destination' => $parsed['open_destination'],
				'closures' => $parsed['closures'],
			);
		}
		return $out;
	}

	public function get_schedule($extension) {
		foreach ($this->list_schedules() as $s) {
			if ($s['extension'] === (int) $extension) {
				return $s;
			}
		}
		return null;
	}

	public static function uuid() {
		if (function_exists('uuid')) {
			return uuid(); // FusionPBX helper, when available
		}
		$d = random_bytes(16);
		$d[6] = chr((ord($d[6]) & 0x0f) | 0x40);
		$d[8] = chr((ord($d[8]) & 0x3f) | 0x80);
		return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($d), 4));
	}
}
