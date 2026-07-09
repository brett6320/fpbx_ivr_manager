<?php
/**
 * Locate the FusionPBX document root and put it on the include path, so the
 * standard "resources/..." includes resolve exactly like core apps do. Walking
 * up to find resources/require.php works on both 4.x and 5.x layouts.
 */
$dir = __DIR__;
for ($i = 0; $i < 8; $i++) {
	if (file_exists($dir . '/resources/require.php')) {
		if (!defined('PROJECT_ROOT')) {
			define('PROJECT_ROOT', $dir);
		}
		set_include_path($dir . PATH_SEPARATOR . get_include_path());
		break;
	}
	$dir = dirname($dir);
}
