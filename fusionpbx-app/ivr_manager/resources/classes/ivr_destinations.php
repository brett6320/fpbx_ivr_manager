<?php
/**
 * Build the list of destinations a call can be sent to, mirroring what FusionPBX
 * offers — including **time conditions**, so a schedule's fall-through (open)
 * destination can point at another time condition (e.g. inbound → closure TC →
 * office-hours TC).
 *
 * Each option's `value` is the number to `transfer <value> XML <domain>`. Queries
 * are individually guarded so a table that differs across FusionPBX 4.x/5.x can't
 * fatal the picker.
 */
class ivr_destinations {

	private $pdo;
	private $domain_uuid;

	public function __construct($pdo, $domain_uuid) {
		$this->pdo = $pdo;
		$this->domain_uuid = $domain_uuid;
	}

	private function query($sql) {
		try {
			$stmt = $this->pdo->prepare($sql);
			$stmt->execute(array(':d' => $this->domain_uuid));
			return $stmt->fetchAll(PDO::FETCH_ASSOC);
		} catch (Exception $e) {
			return array();   // table/column absent on this schema — skip the group
		}
	}

	/** Grouped destinations: array of ['group'=>..., 'options'=>[['value','label'],...]]. */
	public function grouped() {
		$groups = array();

		// Time conditions first — the fall-through target for closure schedules.
		$tc = array();
		foreach ($this->query(
			"select dialplan_number, dialplan_name, dialplan_description from v_dialplans "
			. "where domain_uuid = :d and app_uuid = '" . ivr_schedule::TIME_CONDITIONS_APP_UUID . "' "
			. "order by dialplan_number") as $r) {
			if ($r['dialplan_number'] === null || $r['dialplan_number'] === '' || !is_numeric($r['dialplan_number'])) {
				continue;
			}
			$name = ($r['dialplan_description'] !== '' && $r['dialplan_description'] !== null) ? $r['dialplan_description'] : $r['dialplan_name'];
			$tc[] = array('value' => $r['dialplan_number'], 'label' => $name . ' (' . $r['dialplan_number'] . ')');
		}
		if ($tc) { $groups[] = array('group' => 'Time conditions', 'options' => $tc); }

		$rg = array();
		foreach ($this->query(
			"select ring_group_extension, ring_group_name from v_ring_groups where domain_uuid = :d order by ring_group_extension") as $r) {
			$rg[] = array('value' => $r['ring_group_extension'], 'label' => $r['ring_group_name'] . ' (' . $r['ring_group_extension'] . ')');
		}
		if ($rg) { $groups[] = array('group' => 'Ring groups', 'options' => $rg); }

		$ivr = array();
		foreach ($this->query(
			"select ivr_menu_extension, ivr_menu_name from v_ivr_menus where domain_uuid = :d order by ivr_menu_extension") as $r) {
			$ivr[] = array('value' => $r['ivr_menu_extension'], 'label' => $r['ivr_menu_name'] . ' (' . $r['ivr_menu_extension'] . ')');
		}
		if ($ivr) { $groups[] = array('group' => 'IVR menus', 'options' => $ivr); }

		$ext = array();
		foreach ($this->query(
			"select extension, description from v_extensions where domain_uuid = :d order by extension") as $r) {
			$label = $r['extension'] . (($r['description'] !== '' && $r['description'] !== null) ? ' — ' . $r['description'] : '');
			$ext[] = array('value' => $r['extension'], 'label' => $label);
		}
		if ($ext) { $groups[] = array('group' => 'Extensions', 'options' => $ext); }

		$vm = array();
		foreach ($this->query(
			"select voicemail_id from v_voicemails where domain_uuid = :d order by voicemail_id") as $r) {
			$vm[] = array('value' => '*99' . $r['voicemail_id'], 'label' => 'Voicemail ' . $r['voicemail_id']);
		}
		if ($vm) { $groups[] = array('group' => 'Voicemail', 'options' => $vm); }

		return $groups;
	}

	/** True if $value is one of the known destination values (for prefill logic). */
	public function is_known($value) {
		foreach ($this->grouped() as $g) {
			foreach ($g['options'] as $o) {
				if ((string) $o['value'] === (string) $value) {
					return true;
				}
			}
		}
		return false;
	}

	/**
	 * Render a <select> of destinations (grouped) with an optional manual escape.
	 * $name is the field name; $current preselects a value; adds a "— manual —"
	 * option that pairs with a companion text field named "{$name}_manual".
	 */
	public function render_select($name, $current) {
		$known = false;
		$h = "<select class='formfld' name='" . htmlspecialchars($name, ENT_QUOTES) . "'>";
		$h .= "<option value=''>— none —</option>";
		foreach ($this->grouped() as $g) {
			$h .= "<optgroup label='" . htmlspecialchars($g['group'], ENT_QUOTES) . "'>";
			foreach ($g['options'] as $o) {
				$sel = ((string) $o['value'] === (string) $current) ? ' selected' : '';
				if ($sel !== '') { $known = true; }
				$h .= "<option value='" . htmlspecialchars($o['value'], ENT_QUOTES) . "'" . $sel . ">" . htmlspecialchars($o['label'], ENT_QUOTES) . "</option>";
			}
			$h .= "</optgroup>";
		}
		$h .= "<option value='__manual__'" . (($current !== '' && !$known) ? ' selected' : '') . ">— enter manually —</option>";
		$h .= "</select>";
		$manual_val = ($current !== '' && !$known) ? $current : '';
		$h .= " <input class='formfld' style='width:auto;' name='" . htmlspecialchars($name, ENT_QUOTES) . "_manual' value='" . htmlspecialchars($manual_val, ENT_QUOTES) . "' placeholder='or a number'>";
		return $h;
	}

	/** Resolve a submitted select+manual pair into the destination value. */
	public static function resolve($post, $name) {
		$sel = isset($post[$name]) ? trim($post[$name]) : '';
		$manual = isset($post[$name . '_manual']) ? trim($post[$name . '_manual']) : '';
		if ($sel === '__manual__' || $sel === '') {
			return $manual;
		}
		return $sel;
	}
}
