# YOUR PRODUCT/TEAM NAME
> _Note:_ This document will evolve throughout your project. You commit regularly to this file while working on the project (especially edits/additions/deletions to the _Highlights_ section). 
 > **This document will serve as a master plan between your team, your partner and your TA.**

## Product Details
 
### Q1: What is the product?

**Single sentence, high-level description of the product.**  
We are developing an internal tool that tracks competitors’ online activities such as job listings, reviews, and website changes, and keeps records of the changes over time.

**Problem we are solving in simple terms.**  
To better understand industry trends and stay competitive, SCADABLE needs to know exactly what other companies in the space are doing and charging. This allows the team to position the company clearly for customers and investors, while avoiding being blindsided by new features or pricing updates from competitors.

**Partner**  
Our partner is Ali Rahbar, founder of SCADABLE, a company that provides software for cybersecurity and regulatory compliance. They have already served many businesses.

**What our product is**  
We are planning to build an internal website that tracks competitor activity, including site updates, job listings, and customer reviews. This tool solves the problem of trying to keep up with fast industry changes by hand. 

For example, a competitor like Carbide might post new job listings for frontend developers. This implies they could be planning an overhaul of their interface, prompting SCADABLE to review its own user interface to make sure it stays competitive. Another use case involves customer feedback. If a customer leaves a negative review about a competitor, the tool flags that review for the sales team. The sales team can then highlight those specific weak points during pitches to show why SCADABLE is the better alternative.


### Q2: Who are your target users?

The primary target users are the founders at SCADABLE, who manage strategic positioning, fundraising, and enterprise sales.
* **Pain point:** Currently, they audit competitor websites, public pricing tables, and job boards manually each week. This process is time-consuming, repetitive, and pulls attention away from other core work.
* **Goal using the tool:** Their goal is to replace manual research with a reliable, scheduled system the whole team trusts. They will use a weekly dashboard covering roughly 10 competitors to catch pricing updates, feature rollouts, and hiring trends without opening dozens of tabs by hand.

The secondary target users are the sales team members, who prepare for client calls and product demos.
* **Pain point:** They currently lack a fast way to check what competitors are claiming or charging right before getting on a call.
* **Goal using the tool:** Quickly check how direct competitors position their pricing and feature sets before sales conversations, giving them the facts needed to address customer questions and objections.


### Q3: Why would your users choose your product? What are they using today to solve their problem/need?

The main benefit of this product is saving time for both the founders and the sales team. Today, the founders try to solve this by manually visiting competitor sites every week. Automating this work and putting the results into a clear dashboard will save them several hours each week, removing the need to search competitor sites manually.

This product also provides more accurate and complete information than manual checking. When checking pages by hand, it is easy to miss subtle edits, removed features, or newly posted job roles. By pulling data automatically on a schedule, the tool catches patterns and changes that a person might overlook.

This directly supports SCADABLE’s broader goals. Keeping track of competitors' moves helps the company price its services competitively and spot unmet customer needs, making it much easier to position SCADABLE as the more reliable, cost-effective compliance choice.

#### Q4: What are the user stories that make up the Minumum Viable Product (MVP)?

Add a competitor
- As a founder, I want to add a competitor by entering its name and website in order to track it in one place.
- Acceptance criteria: The name and website are required. After submission, the competitor appears in the list with a pending research status. The app prevents duplicate websites.

Research a competitor
- As a founder, I want the app to research a competitor in order to build a profile without collecting information manually.
- Acceptance criteria: Adding a competitor starts a research run. The app saves a company summary, findings, source links, and the research date. If research fails, the app shows the error and keeps any information already collected.

View the overall SWOT
- As a founder, I want to see a SWOT overview of all tracked competitors in order to understand the competitive landscape.
- Acceptance criteria: The overview separates strengths, weaknesses, opportunities, and threats. Each finding names the relevant competitor and links to its profile. The page shows when the information was last updated.

View a competitor profile
- As a sales team member, I want to open a competitor’s profile in order to prepare for customer conversations.
- Acceptance criteria: The profile shows the competitor’s website, summary, SWOT findings, sources, and latest research date. A finding selected from the overview can be found on the profile.

Compare competitive signals
- As a founder, I want to compare competitors’ product updates, automation, hiring, and announcements in order to spot changes that matter to our company.
- Acceptance criteria: The app groups findings by category and shows the supporting source. Where a finding has a score, it uses the same 0–10 scale across competitors. Missing information is shown as unavailable, not as a score of zero.

Refresh research weekly
- As a founder, I want competitor research to refresh weekly in order to keep the SWOT current.
- Acceptance criteria: Each tracked competitor is researched at least once every seven days while the scheduled process is running. A successful run updates the profile and overview. A failed run is visible and does not delete the last successful findings.

See what changed
- As a sales team member, I want to see new or changed findings after a refresh in order to update our talking points.
- Acceptance criteria: New or changed findings are marked on the competitor profile with their source and detection date. Findings from the first research run are labelled as initial findings.


#### Q5: Have you decided on how you will build it? Share what you know now or tell us the options you are considering.

We have a proposed stack based on the kickoff discussion, but have not finalized every tool. We plan to use Next.js with TypeScript for the web app, Python for the research and AI workflows, and PostgreSQL to store competitors, sources, findings, scores, and research history. This matches the industry partner’s preferred frontend and backend languages and their requirement to use Postgres.

We plan to deploy the Next.js app on Vercel. The Python service and PostgreSQL database will need separate hosting; we are still deciding on a provider. A scheduled job will trigger research for each tracked competitor once a week.

The app will have three main parts:
- Web app: lets users add competitors, view the overall SWOT, and open individual competitor profiles.
- Research service: gathers public information, uses an AI model to summarize and classify findings, and records the sources behind them.
- Database: stores competitor profiles, findings, scores, source links, and past research runs.

The web app will request data from the backend, which will read and write to Postgres. When a user adds a competitor, the backend will queue an initial research run. The weekly scheduled job will use the same research workflow to update existing competitors.

We expect to use the OpenAI API for summarizing research and generating SWOT findings. The industry partner has offered to provide an API key when we are ready to test. We may also use third-party search or news APIs to find public updates, but have not selected one yet. We will review costs and source quality before making that decision. Gemini credits were discussed as a possibility, but Gemini is not currently part of the planned MVP stack.


----
## Intellectual Property Confidentiality Agreement 
> Note this section is **not marked** but must be completed briefly if you have a partner. If you have any questions, please ask on Piazza.
>  
**By default, you own any work that you do as part of your coursework.** However, some partners may want you to keep the project confidential after the course is complete. As part of your first deliverable, you should discuss and agree upon an option with your partner. Examples include:
1. You can share the software and the code freely with anyone with or without a license, regardless of domain, for any use.
2. You can upload the code to GitHub or other similar publicly available domains.
3. You will only share the code under an open-source license with the partner but agree to not distribute it in any way to any other entity or individual. 
4. You will share the code under an open-source license and distribute it as you wish but only the partner can access the system deployed during the course.
5. You will only reference the work you did in your resume, interviews, etc. You agree to not share the code or software in any capacity with anyone unless your partner has agreed to it.

**Your partner cannot ask you to sign any legal agreements or documents pertaining to non-disclosure, confidentiality, IP ownership, etc.**

Briefly describe which option you have agreed to.

----

## Teamwork Details

#### Q6: Have you met with your team?

Do a team-building activity in-person or online. This can be playing an online game, meeting for bubble tea, lunch, or any other activity you all enjoy.
* Get to know each other on a more personal level.
* Provide a few sentences on what you did and share a picture or other evidence of your team building activity.
* Share at least three fun facts from members of you team (total not 3 for each member).


#### Q7: What are the roles & responsibilities on the team?

Describe the different roles on the team and the responsibilities associated with each role (e.g., frontend, database). 
 * Roles should reflect the structure of your team and be appropriate for your project. One person may have multiple roles.  
 * Add role(s) to your Team-[Team_Number]-[Team_Name].csv file on the main folder.
 * At least one person must be identified as the dedicated partner liaison. They need to have great organization and communication skills.
 * Everyone must contribute to code. Students who don't contribute to code enough will receive a lower mark at the end of the term.

List each team member and:
 * A description of their role(s) and responsibilities including the components they'll work on and non-software related work
 * Why did you choose them to take that role? Specify if they are interested in learning that part, experienced in it, or any other reasons. Do no make things up. This part is not graded but may be reviewed later.


#### Q8: How will you work as a team?

Describe meetings (and other events) you are planning to have. 
 * When and where? Recurring or ad hoc? In-person or online?
 * What's the purpose of each meeting?
 * Other events could be coding sessions, code reviews, quick weekly sync meeting online, etc.
 * You should have 2 meetings with your project partner (if you have one) before D1 is due. Describe them here:
   * You must keep track of meeting minutes and add them to your repo under "deliverables/minutes" folder
   * You must have a regular meeting schedule established for the rest of the term.  
  
#### Q9: How will you organize your team?

- **GitHub Issues/Project Board**
  - Track tasks, bugs, priorities, assignees, deadlines, and status.
  - TA and partner will have access to monitor progress.
- **GitHub branches and pull requests**
  - Each technical task will have its own branch.
  - Pull requests will be used for code review and tracking completed work.
- **Discord**
  - Main platform for team communication, updates, roadblocks, task coordination, and meeting scheduling.
- **Meeting minutes**
  - Shared Google Doc documenting decisions, action items, owners, and deadlines.
- **Task prioritization**
  - Based on project requirements, deadlines, and partner feedback.
  - Core-functionality tasks for the MVP will be prioritized first.
- **Task assignment**
  - Assigned based on each member’s project role, skills, workload, and availability.
- **Task status**
  - Tracked as: **Backlog → In Progress → Review → Complete**.
  - Progress will also be visible through commits, branches, pull requests, and Discord updates.

#### Q10: What are the rules regarding how your team works?

**Communication**
- Discord will be used daily for updates, questions, roadblocks, and task coordination.
- We will hold approximately **1–2 team calls per week** for progress updates and next steps.
- Our partner will be contacted through **WhatsApp** for direct communication.
- We will meet with the partner **weekly or biweekly** to review requirements, progress, and feedback.
- One team member will act as the main partner contact and share relevant updates with the team.

**Collaboration and Accountability**
- Every task will have a clear owner and expected completion date.
- Team members should notify the group in advance if they cannot attend a meeting.
- If someone falls behind:
  - Discuss the issue directly and identify any blockers.
  - Redistribute work if needed to avoid blocking the project.
- If missed work or lack of communication continues, the issue will be documented and discussed with the **TA/course staff** if it cannot be resolved internally.

## Organisation Details

#### Q11. How does your team fit within the overall team organisation of the partner?

Our team will primarily play a product development role within SCADABLE by designing and building a new internal competitor-tracking platform from scratch. We will be responsible for the full development process, including the frontend, backend, database, data collection, change detection, integrating APIs and testing. Although the platform will initially be separate from SCADABLE’s existing product and repositories, it is being built specifically for their founders and sales team, with each team member owning a distinct part of the system and receiving regular feedback and code review from the founder.

#### Q12. How does your project fit within the overall product from the partner?

Our project is a standalone internal platform that our team will build end to end, rather than a feature within SCADABLE’s existing product. The system will track around ten real competitors, display changes across sources such as websites, job postings, press releases and reviews, and ideally allow users to add new competitors for automatic monitoring. By the end of the term, SCADABLE expects a functional and usable product with a frontend, backend, database, scheduled data collection, reliable change detection and source-linked information. In the future, the platform may be integrated with SCADABLE’s internal AI agent infrastructure.

## Potential Risks

#### Q13. What are some potential risks to your project?
* Now that you have defined your project, what risks can you identify that might impact it?
* Some examples of risks at this planning stage could include:
  * Uncertainties regarding a specific feature
  * Misaligned expectations or conflicts
  * Lack of clarity in execution or decision-making
  * Limited access to data, systems, or other dependencies
  * User stories that are too abstract or too simple
* For each risk, provide a brief bullet point and then explain the risk in detail. 

#### Q14. What are some potential mitigation strategies for the risks you identified?
* Examples of mitigation strategies:
  * More communication with the partner might help with improving clarity.
  * Adding more details for an user story might make it less abstract.
  * Adding an extra user story might increase the project complexity, making it less simple.
* It's ok if you are unable to find mitigation strategies for all the risks right now.
