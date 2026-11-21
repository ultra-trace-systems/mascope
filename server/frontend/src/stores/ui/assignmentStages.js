import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

/**
 * Whether the batch's Assign batch menu shows its Advanced group: the batch
 * stages one by one, each starting the pass at that stage
 * (`BatchAssignControl.vue`). A user setting, off by default, so a lab user
 * never meets stages they did not ask for while whoever checks a stage can
 * still reach one. Who sees Advanced is the plan owner's call
 * (docs/dev/batch_assignment_one_action.md, 7.2); the setting is how it is
 * built until that call is made.
 *
 * Kept in this browser, as the theme and the Targets/Assignments choice are:
 * it changes what one person's menu offers, nothing the server does.
 */
const KEY = 'mascope.assignment.showStages'

export const useAssignmentStages = defineStore('app.ui.assignmentStages', () => {
  const show = ref(localStorage.getItem(KEY) === 'true')
  watch(show, (value) => localStorage.setItem(KEY, value ? 'true' : 'false'))
  return { show }
})
