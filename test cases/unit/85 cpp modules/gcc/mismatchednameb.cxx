module matchedname:B; // partition module implementation unit
 
// WorldImpl() is visible by any module unit of 'matchedname' importing ':B'.
char const* WorldImpl() { return "World"; }
